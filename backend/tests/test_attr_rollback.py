"""属性字典版本回滚、撤回记录与标签复核的领域测试。"""
from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import attr_version as av
from app.services.tag_define import (
    confirm_conditions,
    reopen_validation,
    validate_tag_at_point_in_time,
)
from app.store import store

VERSION_TABLE = av.VERSION_TABLE
WITHDRAW_TABLE = av.WITHDRAW_TABLE
BINDING_TABLE = av.BINDING_TABLE


def _snapshot_tables() -> dict[str, list[dict]]:
    return {
        name: copy.deepcopy(store.rows(name))
        for name in ("attr_version", "attr_withdraw", "tag_condition_binding",
                     "attr_dict", "tag_define")
    }


class AttrRollbackTestBase(unittest.TestCase):
    """每个用例前后还原相关表，保证用例间隔离。"""

    def setUp(self) -> None:
        self._backup = _snapshot_tables()
        store._artifacts.clear()
        store._compute_cache.clear()
        store._snapshots.clear()

    def tearDown(self) -> None:
        for name, rows in self._backup.items():
            table = store.rows(name)
            table.clear()
            table.extend(copy.deepcopy(rows))

    def _publish_enum_version(self) -> dict:
        """把 ATTR-002（int）覆盖成 enum，制造字段类型变更。"""
        return av.publish_version(
            attr_id="ATTR-002",
            definition={
                "attr_name": "最近活跃天数",
                "data_type": "enum",
                "enum_values": ["低", "中", "高"],
                "source": "derived",
                "update_freq": "daily",
                "attribution": "last",
                "version": "v2.0",
            },
            effective_from="2026-11-15T00:00:00",
            change_type="update",
            remark="字段类型由 int 改为 enum",
        )

    def _make_enum_tag(self) -> dict:
        """登记一个只在 enum 类型下成立的标签条件。"""
        return store.insert("tag_define", {
            "status": "已生效",
            "pending": False,
            "abnormal": False,
            "tag_id": "TAG-ENUM-1",
            "tag_name": "活跃度分层用户",
            "tag_level": "derived",
            "compute_logic": "ATTR-002 IN ['低', '中']",
            "source_attrs": ["ATTR-002"],
            "update_cycle": "daily",
            "coverage_threshold": 0.8,
            "version": "v1.0",
        })


class TestVersionTimeline(AttrRollbackTestBase):
    """版本写入与时点查询。"""

    def test_publish_closes_previous_open_interval(self) -> None:
        self._publish_enum_version()
        versions = av.list_versions("ATTR-002")
        self.assertEqual(len(versions), 2)
        initial, updated = versions
        # 初始版本区间被关闭，终点即新版本生效时间
        self.assertEqual(initial["effective_to"], "2026-11-15T00:00:00")
        self.assertEqual(initial["superseded_by"], updated["version_id"])
        self.assertIsNone(updated["effective_to"])

    def test_as_of_returns_unique_type_per_point_in_time(self) -> None:
        self._publish_enum_version()
        before = av.get_version_at("ATTR-002", "2026-11-14T12:00:00")
        after = av.get_version_at("ATTR-002", "2026-11-16T12:00:00")
        boundary = av.get_version_at("ATTR-002", "2026-11-15T00:00:00")
        self.assertEqual(before["data_type"], "int")
        self.assertEqual(after["data_type"], "enum")
        # 半开区间：边界时刻属于新版本
        self.assertEqual(boundary["data_type"], "enum")
        self.assertEqual(boundary["version_id"], after["version_id"])

    def test_no_two_versions_overlap_at_same_point(self) -> None:
        """同一时点的校验请求不能读到相互矛盾的字段类型。"""
        self._publish_enum_version()
        probes = ["2026-11-01T00:00:00", "2026-11-14T23:59:59",
                  "2026-11-15T00:00:00", "2026-11-20T08:30:00"]
        for pit in probes:
            hits = [
                v for v in av.list_versions("ATTR-002")
                if av.parse_time(v["effective_from"]) <= av.parse_time(pit)
                and (v["effective_to"] is None
                     or av.parse_time(v["effective_to"]) > av.parse_time(pit))
            ]
            self.assertEqual(len(hits), 1, f"时点 {pit} 命中了多个相互矛盾的版本")

    def test_publish_rejects_earlier_effective_time(self) -> None:
        self._publish_enum_version()
        with self.assertRaises(ValueError):
            av.publish_version(
                attr_id="ATTR-002",
                definition={"data_type": "string"},
                effective_from="2026-11-10T00:00:00",
            )

    def test_publish_syncs_current_attr_dict_row(self) -> None:
        self._publish_enum_version()
        attr = store.find_by_field("attr_dict", "attr_id", "ATTR-002")
        self.assertEqual(attr["data_type"], "enum")
        self.assertEqual(attr["enum_values"], ["低", "中", "高"])


class TestWithdrawAndReview(AttrRollbackTestBase):
    """撤回回滚、撤回记录与候选标签复核。"""

    def test_withdraw_writes_rollback_version_and_record(self) -> None:
        self._publish_enum_version()
        target = av.list_versions("ATTR-002")[0]
        record = av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )
        # 回滚版本按生效时间写入
        current = av.get_current_version("ATTR-002")
        self.assertEqual(current["change_type"], "rollback")
        self.assertEqual(current["data_type"], "int")
        self.assertEqual(current["effective_from"], "2026-11-18T00:00:00")
        self.assertEqual(current["withdraw_id"], record["withdraw_id"])

        # 撤回记录可回查
        fetched = av.get_withdraw(record["withdraw_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["restored_data_type"], "int")
        self.assertEqual(fetched["withdrawn_data_type"], "enum")

    def test_withdrawn_interval_vs_review_candidates_separated(self) -> None:
        enum_version = self._publish_enum_version()
        self._make_enum_tag()
        target = av.list_versions("ATTR-002")[0]
        record = av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )

        # 真正撤回的区间：enum 版本从生效到撤回时刻被关闭
        self.assertEqual(len(record["withdrawn_intervals"]), 1)
        interval = record["withdrawn_intervals"][0]
        self.assertEqual(interval["version_id"], enum_version["version_id"])
        self.assertEqual(interval["effective_from"], "2026-11-15T00:00:00")
        self.assertEqual(interval["effective_to"], "2026-11-18T00:00:00")
        self.assertEqual(interval["data_type"], "enum")

        # 复核候选与撤回区间是两个独立字段
        candidate_ids = [c["tag_id"] for c in record["review_candidates"]]
        self.assertIn("TAG-ENUM-1", candidate_ids)
        candidate = next(c for c in record["review_candidates"] if c["tag_id"] == "TAG-ENUM-1")
        self.assertTrue(candidate["has_conflict"])

        # 冲突条件说明字段类型：enum → int，IN 不再受支持
        conflict = candidate["conflicting_conditions"][0]
        self.assertEqual(conflict["operator"], "IN")
        self.assertEqual(conflict["withdrawn_data_type"], "enum")
        self.assertEqual(conflict["restored_data_type"], "int")
        self.assertIn("字段类型冲突", conflict["conflict_reason"])

    def test_old_type_supported_conditions_remain_usable(self) -> None:
        """旧类型（int）仍支持的比较条件不应进入复核候选。"""
        self._publish_enum_version()
        self._make_enum_tag()
        target = av.list_versions("ATTR-002")[0]
        record = av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )
        unaffected_ids = {t["tag_id"] for t in record["unaffected_tags"]}
        # TAG-001/TAG-002/TAG-004 都是数值比较，int 恢复后仍然可用
        self.assertIn("TAG-001", unaffected_ids)
        self.assertIn("TAG-002", unaffected_ids)
        self.assertIn("TAG-004", unaffected_ids)
        self.assertNotIn("TAG-ENUM-1", unaffected_ids)

        # 未受影响标签不会被改成待复核
        tag1 = store.find_by_field("tag_define", "tag_id", "TAG-001")
        self.assertEqual(tag1["status"], "已生效")

    def test_conflicted_tag_marked_pending_review(self) -> None:
        self._publish_enum_version()
        self._make_enum_tag()
        target = av.list_versions("ATTR-002")[0]
        record = av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )
        enum_tag = store.find_by_field("tag_define", "tag_id", "TAG-ENUM-1")
        self.assertEqual(enum_tag["status"], "待复核")
        self.assertTrue(enum_tag["abnormal"])
        self.assertEqual(enum_tag["last_withdraw_id"], record["withdraw_id"])


class TestConfirmedBindingKeepsEvidence(AttrRollbackTestBase):
    """已确认标签条件保留时点与原字段来源。"""

    def test_confirm_locks_point_in_time_and_source_version(self) -> None:
        self._publish_enum_version()
        # 先登记 enum 标签再确认
        self._make_enum_tag()
        binding = confirm_conditions("TAG-ENUM-1", point_in_time="2026-11-16T10:00:00")
        self.assertEqual(binding["status"], "confirmed")
        self.assertEqual(binding["point_in_time"], "2026-11-16T10:00:00")
        locked = binding["conditions"][0]
        self.assertEqual(locked["attr_id"], "ATTR-002")
        self.assertEqual(locked["source_data_type"], "enum")
        self.assertTrue(locked["source_version_id"].startswith("AV-002"))

    def test_withdraw_does_not_rewrite_confirmed_evidence(self) -> None:
        self._publish_enum_version()
        self._make_enum_tag()
        binding = confirm_conditions("TAG-ENUM-1", point_in_time="2026-11-16T10:00:00")
        target = av.list_versions("ATTR-002")[0]
        record = av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )
        stored = store.find_by_field(BINDING_TABLE, "binding_id", binding["binding_id"])
        # 时点与原字段来源原样保留
        self.assertEqual(stored["point_in_time"], "2026-11-16T10:00:00")
        self.assertEqual(stored["conditions"][0]["source_data_type"], "enum")
        # 冲突作为附加证据，不覆盖确认信息
        self.assertEqual(stored["review_status"], "pending")
        self.assertEqual(stored["last_withdraw_id"], record["withdraw_id"])
        self.assertTrue(stored["conflicts"])
        self.assertIn("字段类型冲突", stored["conflicts"][0]["conflict_reason"])

    def test_point_in_time_validation_uses_locked_timeline(self) -> None:
        self._publish_enum_version()
        self._make_enum_tag()
        target = av.list_versions("ATTR-002")[0]
        av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )
        # enum 生效期间：IN 合法
        ok_report = validate_tag_at_point_in_time("TAG-ENUM-1", "2026-11-16T10:00:00")
        self.assertTrue(ok_report["valid"])
        # 回滚后：int 不支持 IN，校验暴露类型冲突
        bad_report = validate_tag_at_point_in_time("TAG-ENUM-1", "2026-11-19T10:00:00")
        self.assertFalse(bad_report["valid"])
        self.assertEqual(bad_report["type_conflicts"][0]["data_type"], "int")
        # 数值比较标签在回滚后重新可用
        recovered = validate_tag_at_point_in_time("TAG-002", "2026-11-19T10:00:00")
        self.assertTrue(recovered["valid"])


class TestReopenValidationAndPeriods(AttrRollbackTestBase):
    """重新打开校验与按周期回查版本。"""

    def _withdraw_once(self) -> dict:
        self._publish_enum_version()
        self._make_enum_tag()
        target = av.list_versions("ATTR-002")[0]
        return av.withdraw_attr(
            attr_id="ATTR-002",
            target_version_id=target["version_id"],
            effective_time="2026-11-18T00:00:00",
        )

    def test_reopen_validation_explains_conflicting_types(self) -> None:
        record = self._withdraw_once()
        report = reopen_validation(record["withdraw_id"])
        self.assertEqual(len(report["candidates"]), 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["tag_id"], "TAG-ENUM-1")
        self.assertTrue(candidate["review_open"])
        self.assertEqual(candidate["current_data_type"], "int")
        still = candidate["still_conflicting"]
        self.assertEqual(len(still), 1)
        self.assertIn("字段类型冲突", still[0]["conflict_reason"])
        # 未受影响标签单独列出
        self.assertIn("TAG-001", report["unaffected_tag_ids"])

    def test_period_lookup_shows_versions_used_each_cycle(self) -> None:
        self._withdraw_once()
        result = av.get_versions_by_period(
            "ATTR-002", "day", "2026-11-14", "2026-11-19",
        )
        self.assertEqual(len(result["cycles"]), 5)
        by_day = {c["cycle_key"]: c for c in result["cycles"]}
        self.assertEqual(by_day["2026-11-14"]["effective_data_type"], "int")
        self.assertEqual(by_day["2026-11-15"]["effective_data_type"], "enum")
        self.assertEqual(by_day["2026-11-17"]["effective_data_type"], "enum")
        self.assertEqual(by_day["2026-11-18"]["effective_data_type"], "int")
        # 半开区间：11-15 当天 int 版本恰好在零点结束，周期内只有 enum 版本，不产生矛盾
        version_ids = {v["version_id"] for v in by_day["2026-11-15"]["available_versions"]}
        self.assertEqual(len(version_ids), 1)
        self.assertEqual(by_day["2026-11-15"]["available_versions"][0]["data_type"], "enum")
        # 11-14 当天仍只有旧 int 版本
        self.assertEqual(len(by_day["2026-11-14"]["available_versions"]), 1)

    def test_week_and_month_grain_supported(self) -> None:
        self._withdraw_once()
        weekly = av.get_versions_by_period("ATTR-002", "week", "2026-11-09", "2026-11-23")
        self.assertEqual(len(weekly["cycles"]), 2)
        monthly = av.get_versions_by_period("ATTR-002", "month", "2026-11-01", "2026-12-01")
        self.assertEqual(len(monthly["cycles"]), 1)
        self.assertGreaterEqual(len(monthly["cycles"][0]["available_versions"]), 3)

    def test_invalid_grain_rejected(self) -> None:
        with self.assertRaises(ValueError):
            av.get_versions_by_period("ATTR-002", "hour", "2026-11-14", "2026-11-19")

    def test_withdraw_rejects_same_target_as_current(self) -> None:
        self._publish_enum_version()
        current = av.get_current_version("ATTR-002")
        with self.assertRaises(ValueError):
            av.withdraw_attr("ATTR-002", current["version_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
