"""属性字段版本与撤回回滚服务。

设计要点：
- 属性定义按 ``effective_from/effective_to`` 保留为半开区间版本（[from, to)），
  任一时刻对同一属性只可能命中一个生效版本，保证时点校验读不到相互矛盾的字段类型。
- 撤回（回滚）会按生效时间写入一条回滚版本，关闭被撤回版本的生效区间，
  并生成撤回记录，把“真正撤回的区间”和“需要复核的候选标签”分开留痕。
- 已确认的标签条件通过 ``tag_condition_binding`` 锁定确认时点与原字段来源版本，
  回滚不篡改这些证据；冲突只做附加标记，复核通过后可重新确认绑定。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

VERSION_TABLE = "attr_version"
WITHDRAW_TABLE = "attr_withdraw"
BINDING_TABLE = "tag_condition_binding"

# 各数据类型支持的条件操作符
TYPE_OPERATORS: dict[str, set[str]] = {
    "int": {"=", "!=", "<", ">", "<=", ">="},
    "float": {"=", "!=", "<", ">", "<=", ">="},
    "datetime": {"=", "!=", "<", ">", "<=", ">="},
    "enum": {"=", "!=", "IN", "NOT IN"},
    "string": {"=", "!=", "IN", "NOT IN"},
    "bool": {"=", "!=", "IN", "NOT IN"},
}
NUMERIC_TYPES = {"int", "float"}
COMPARISON_OPS = ["<=", ">=", "!=", "=", "<", ">"]


# ========== 时间工具 ==========
def parse_time(value: str) -> datetime:
    """解析 ISO 风格时间字符串，兼容只给日期的写法。"""
    text = value.strip()
    if len(text) == 10:
        text = f"{text}T00:00:00"
    return datetime.fromisoformat(text)


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# ========== 版本查询 ==========
def list_versions(attr_id: str) -> list[dict[str, Any]]:
    """列出某属性的全部历史版本，按生效时间升序。"""
    rows = [
        dict(row)
        for row in store.rows(VERSION_TABLE)
        if row.get("attr_id") == attr_id
    ]
    return sorted(rows, key=lambda r: parse_time(r["effective_from"]))


def get_current_version(attr_id: str) -> dict[str, Any] | None:
    """获取当前生效版本（生效区间未关闭的那条）。"""
    for row in store.rows(VERSION_TABLE):
        if row.get("attr_id") == attr_id and row.get("effective_to") is None:
            return dict(row)
    return None


def get_version(version_id: str) -> dict[str, Any] | None:
    return store.find_by_field(VERSION_TABLE, "version_id", version_id)


def get_version_at(attr_id: str, point_in_time: str) -> dict[str, Any] | None:
    """时点查询：返回该时点唯一生效的属性版本。

    半开区间 [effective_from, effective_to) 保证同一时点最多命中一个版本，
    因此校验请求不会读到相互矛盾的字段类型。
    """
    pit = parse_time(point_in_time)
    for row in store.rows(VERSION_TABLE):
        if row.get("attr_id") != attr_id:
            continue
        start = parse_time(row["effective_from"])
        if start > pit:
            continue
        end_text = row.get("effective_to")
        if end_text is None or parse_time(end_text) > pit:
            return dict(row)
    return None


def get_versions_by_period(
    attr_id: str,
    period_grain: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """按周期回查各周期可用的属性字段版本。

    period_grain 支持 day/week/month；每个周期给出周期起点生效版本
    以及与周期区间相交的全部可用版本。
    """
    grain = period_grain.lower()
    if grain not in {"day", "week", "month"}:
        raise ValueError("period_grain 仅支持 day/week/month")

    cursor = parse_time(start)
    end_at = parse_time(end)
    versions = list_versions(attr_id)
    cycles: list[dict[str, Any]] = []

    while cursor < end_at:
        if grain == "day":
            next_cursor = cursor + timedelta(days=1)
            key = cursor.strftime("%Y-%m-%d")
        elif grain == "week":
            next_cursor = cursor + timedelta(weeks=1)
            key = f"{cursor.strftime('%Y-%m-%d')}周"
        else:
            if cursor.month == 12:
                next_cursor = cursor.replace(year=cursor.year + 1, month=1, day=1)
            else:
                next_cursor = cursor.replace(month=cursor.month + 1, day=1)
            key = cursor.strftime("%Y-%m")

        available: list[dict[str, Any]] = []
        for ver in versions:
            v_start = parse_time(ver["effective_from"])
            v_end = parse_time(ver["effective_to"]) if ver.get("effective_to") else end_at
            # 区间相交即该周期内可用过
            if v_start < next_cursor and v_end > cursor:
                available.append({
                    "version_id": ver["version_id"],
                    "data_type": ver["data_type"],
                    "change_type": ver["change_type"],
                    "effective_from": ver["effective_from"],
                    "effective_to": ver.get("effective_to"),
                })
        head = get_version_at(attr_id, cursor.isoformat())
        cycles.append({
            "cycle_key": key,
            "cycle_start": cursor.isoformat(),
            "cycle_end": next_cursor.isoformat(),
            "effective_at_cycle_start": head["version_id"] if head else None,
            "effective_data_type": head["data_type"] if head else None,
            "available_versions": available,
        })
        cursor = next_cursor

    return {
        "attr_id": attr_id,
        "period_grain": grain,
        "start": start,
        "end": end,
        "cycles": cycles,
    }


# ========== 版本写入 ==========
def _next_version_id(attr_id: str) -> str:
    seq = sum(1 for row in store.rows(VERSION_TABLE) if row.get("attr_id") == attr_id) + 1
    number = attr_id.split("-", 1)[1] if "-" in attr_id else attr_id
    return f"AV-{number}-{seq:03d}"


def _definition_fields() -> tuple[str, ...]:
    return (
        "attr_name", "data_type", "enum_values", "source",
        "update_freq", "attribution",
    )


def publish_version(
    attr_id: str,
    definition: dict[str, Any],
    effective_from: str,
    change_type: str = "update",
    remark: str = "",
    withdraw_id: str | None = None,
) -> dict[str, Any]:
    """按生效时间写入一条新的属性字段版本。

    写入前关闭当前开放区间（旧版本的 effective_to 指向新生效时间），
    保证版本区间首尾相接、互不重叠。
    """
    effective_at = parse_time(effective_from)
    current = get_current_version(attr_id)
    if current is not None:
        if parse_time(current["effective_from"]) >= effective_at:
            raise ValueError("新生效时间必须晚于当前版本的生效时间")
        new_version_id = _next_version_id(attr_id)
        # 关闭旧版本开放区间
        for row in store.rows(VERSION_TABLE):
            if row.get("version_id") == current["version_id"]:
                row["effective_to"] = effective_at.isoformat()
                row["superseded_by"] = new_version_id
                break
    else:
        new_version_id = _next_version_id(attr_id)

    version_row: dict[str, Any] = {
        "version_id": new_version_id,
        "attr_id": attr_id,
        "change_type": change_type,
        "effective_from": effective_at.isoformat(),
        "effective_to": None,
        "superseded_by": None,
        "withdraw_id": withdraw_id,
        "snapshot_version": definition.get("version") or f"v{_version_seq(attr_id)}.0",
        "remark": remark,
    }
    for field in _definition_fields():
        if field in definition:
            version_row[field] = definition[field]
    # 兜底，避免缺字段
    version_row.setdefault("attr_name", current.get("attr_name", "") if current else "")
    version_row.setdefault("enum_values", [])

    store.insert(VERSION_TABLE, version_row)

    # 同步当前定义到 attr_dict 主表（旧字段被覆盖，但历史版本仍可回查）
    attr_row = store.find_by_field("attr_dict", "attr_id", attr_id)
    if attr_row is not None:
        sync_fields = {f: version_row[f] for f in _definition_fields() if f in version_row}
        sync_fields["version"] = new_version_id
        store.update("attr_dict", attr_row["id"], sync_fields)

    return dict(version_row)


def _version_seq(attr_id: str) -> int:
    return sum(1 for row in store.rows(VERSION_TABLE) if row.get("attr_id") == attr_id) + 1


# ========== 标签条件解析与兼容性 ==========
def parse_conditions(logic: str) -> list[dict[str, Any]]:
    """把标签计算逻辑拆成原子条件（只做顶层 AND/OR 拆分，与计算引擎风格一致）。"""
    tokens = logic.split()
    atoms: list[str] = []
    buffer: list[str] = []
    depth = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        upper = tok.upper()
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        if depth == 0 and upper in {"AND", "OR"}:
            if buffer:
                atoms.append(" ".join(buffer).strip())
                buffer = []
        else:
            buffer.append(tok)
        i += 1
    if buffer:
        atoms.append(" ".join(buffer).strip())
    if not atoms:
        atoms = [logic.strip()]

    conditions: list[dict[str, Any]] = []
    for atom in atoms:
        parsed = _parse_atom(atom.strip("() "))
        if parsed is not None:
            conditions.append(parsed)
    return conditions


def _parse_atom(atom: str) -> dict[str, Any] | None:
    upper = atom.upper()
    if " IN " in upper:
        pos = upper.index(" IN ")
        field = atom[:pos].strip()
        operator = "NOT IN" if "NOT IN" in upper else "IN"
        raw_value = atom[atom.upper().index(operator) + len(operator):].strip()
        return {"field": field, "operator": operator, "raw_value": raw_value}
    for op in COMPARISON_OPS:
        if op in atom:
            left, right = atom.split(op, 1)
            return {
                "field": left.strip(),
                "operator": op,
                "raw_value": right.strip().strip("'\""),
            }
    # 裸字段（布尔标签引用等），无法做类型校验时忽略
    return None


def operator_supported(data_type: str, operator: str) -> bool:
    return operator in TYPE_OPERATORS.get(data_type, set())


def check_condition(condition: dict[str, Any], data_type: str) -> dict[str, Any]:
    """校验单个条件与字段类型是否兼容，返回带说明的结果。"""
    supported = operator_supported(data_type, condition["operator"])
    result = dict(condition)
    result["data_type"] = data_type
    result["supported"] = supported
    if not supported:
        allowed = sorted(TYPE_OPERATORS.get(data_type, set()))
        result["conflict_reason"] = (
            f"字段类型冲突：当前类型为 {data_type}，"
            f"不支持操作符 {condition['operator']}，允许的操作符为 {', '.join(allowed)}"
        )
    return result


def _attr_conditions_for(tag: dict[str, Any], attr_id: str) -> list[dict[str, Any]]:
    conditions = parse_conditions(tag.get("compute_logic", ""))
    return [c for c in conditions if c["field"] == attr_id]


# ========== 标签条件确认（锁定时点与原字段来源） ==========
def _next_binding_id() -> str:
    seq = len(store.rows(BINDING_TABLE)) + 1
    return f"BIND-{seq:03d}"


def confirm_tag_conditions(
    tag_id: str,
    point_in_time: str | None = None,
    remark: str = "",
) -> dict[str, Any]:
    """确认标签条件：按确认时点锁定每个属性字段的来源版本与类型。

    确认后条件的时点和原字段来源被固定，后续属性回滚不会改写这份证据。
    """
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    if tag is None:
        raise ValueError(f"Tag not found: {tag_id}")

    pit = point_in_time or now_iso()
    parse_time(pit)  # 校验格式
    locked: list[dict[str, Any]] = []
    missing: list[str] = []
    for attr_id in tag.get("source_attrs", []):
        if not attr_id.startswith("ATTR-"):
            continue  # 标签间引用（TAG-xxx）不在属性版本管理范围内
        version = get_version_at(attr_id, pit)
        if version is None:
            missing.append(attr_id)
            continue
        for cond in _attr_conditions_for(tag, attr_id):
            locked.append({
                "attr_id": attr_id,
                "operator": cond["operator"],
                "raw_value": cond["raw_value"],
                "source_version_id": version["version_id"],
                "source_data_type": version["data_type"],
            })

    binding = {
        "binding_id": _next_binding_id(),
        "tag_id": tag_id,
        "status": "confirmed",
        "confirmed_at": now_iso(),
        "point_in_time": pit,
        "logic_snapshot": tag.get("compute_logic", ""),
        "conditions": locked,
        "withdraw_id": None,
        "conflicts": [],
        "remark": remark or "标签条件确认，锁定时点与原字段来源",
    }
    if missing:
        binding["missing_attrs"] = missing
    store.insert(BINDING_TABLE, binding)
    return dict(binding)


def list_bindings(tag_id: str | None = None) -> list[dict[str, Any]]:
    rows = store.rows(BINDING_TABLE)
    if tag_id is not None:
        rows = [r for r in rows if r.get("tag_id") == tag_id]
    return [dict(r) for r in rows]


# ========== 撤回（回滚） ==========
def _next_withdraw_id() -> str:
    return f"WD-{len(store.rows(WITHDRAW_TABLE)) + 1:03d}"


def withdraw_attr(
    attr_id: str,
    target_version_id: str,
    effective_time: str | None = None,
    remark: str = "",
) -> dict[str, Any]:
    """撤回当前字段定义，按生效时间恢复为目标历史版本的定义。

    流程：
    1. 关闭被撤回版本的开放区间，区间终点即撤回生效时间（真正撤回的区间）；
    2. 按生效时间写入一条 change_type=rollback 的回滚版本；
    3. 扫描引用该属性的标签登记，区分旧类型仍支持的条件与冲突候选；
    4. 生成撤回记录并在冲突标签/绑定上附加复核标记（不篡改已确认证据）。
    """
    target = get_version(target_version_id)
    if target is None or target.get("attr_id") != attr_id:
        raise ValueError("目标回滚版本不存在或不属于该属性")
    current = get_current_version(attr_id)
    if current is None:
        raise ValueError("该属性没有可撤回的当前生效版本")
    if current["version_id"] == target_version_id:
        raise ValueError("目标版本与当前版本一致，无需撤回")

    effective_at = parse_time(effective_time or now_iso())
    if effective_at <= parse_time(current["effective_from"]):
        raise ValueError("撤回生效时间必须晚于当前版本的生效时间")
    effective_iso = effective_at.isoformat()
    withdraw_id = _next_withdraw_id()

    # 1. 关闭被撤回版本的开放区间 → 真正撤回的区间
    withdrawn_interval = {
        "version_id": current["version_id"],
        "data_type": current["data_type"],
        "change_type": current["change_type"],
        "effective_from": current["effective_from"],
        "effective_to": effective_iso,
    }
    for row in store.rows(VERSION_TABLE):
        if row.get("version_id") == current["version_id"]:
            row["effective_to"] = effective_iso
            row["withdraw_id"] = withdraw_id
            break

    # 2. 写入回滚版本（定义取自目标历史版本）
    restored_definition = {field: target.get(field) for field in _definition_fields()}
    rollback_version = publish_version(
        attr_id=attr_id,
        definition=restored_definition,
        effective_from=effective_iso,
        change_type="rollback",
        remark=f"回滚恢复自 {target_version_id}",
        withdraw_id=withdraw_id,
    )

    # 3. 扫描引用该属性的标签登记，区分仍可用条件与复核候选
    review_candidates: list[dict[str, Any]] = []
    unaffected_tags: list[dict[str, Any]] = []
    for tag in store.rows("tag_define"):
        if attr_id not in tag.get("source_attrs", []):
            continue
        tag_entry = _evaluate_tag_against(tag, attr_id, current, rollback_version)
        if tag_entry["has_conflict"]:
            review_candidates.append(tag_entry)
        else:
            unaffected_tags.append(tag_entry)

    # 4. 在已确认绑定上附加冲突证据（保留时点与原字段来源），并把冲突标签置为待复核
    for candidate in review_candidates:
        for binding in store.rows(BINDING_TABLE):
            if binding.get("tag_id") != candidate["tag_id"]:
                continue
            new_conflicts = [
                dict(c, withdraw_id=withdraw_id) for c in candidate["conflicting_conditions"]
            ]
            binding.setdefault("conflicts", []).extend(new_conflicts)
            binding["review_status"] = "pending"
            binding["last_withdraw_id"] = withdraw_id
        tag_row = store.find_by_field("tag_define", "tag_id", candidate["tag_id"])
        if tag_row is not None:
            store.update("tag_define", tag_row["id"], {
                "status": "待复核",
                "pending": True,
                "abnormal": True,
                "last_withdraw_id": withdraw_id,
            })

    record = {
        "withdraw_id": withdraw_id,
        "attr_id": attr_id,
        "status": "completed",
        "effective_time": effective_iso,
        "created_at": now_iso(),
        "withdrawn_version_id": current["version_id"],
        "withdrawn_data_type": current["data_type"],
        "target_version_id": target_version_id,
        "rollback_version_id": rollback_version["version_id"],
        "restored_data_type": rollback_version["data_type"],
        # 真正撤回的区间（与复核候选严格分开）
        "withdrawn_intervals": [withdrawn_interval],
        "review_candidate_count": len(review_candidates),
        "review_candidates": review_candidates,
        "unaffected_tags": unaffected_tags,
        "remark": remark or f"属性 {attr_id} 撤回至 {target_version_id} 的字段定义",
    }
    store.insert(WITHDRAW_TABLE, record)
    return dict(record)


def _evaluate_tag_against(
    tag: dict[str, Any],
    attr_id: str,
    withdrawn_version: dict[str, Any],
    restored_version: dict[str, Any],
) -> dict[str, Any]:
    """评估单个标签在撤回后的条件兼容性，冲突条件带字段类型说明。"""
    conditions = _attr_conditions_for(tag, attr_id)
    conflicting: list[dict[str, Any]] = []
    supported: list[dict[str, Any]] = []
    for cond in conditions:
        checked = check_condition(cond, restored_version["data_type"])
        entry = {
            "attr_id": attr_id,
            "operator": cond["operator"],
            "raw_value": cond["raw_value"],
            "withdrawn_data_type": withdrawn_version["data_type"],
            "restored_data_type": restored_version["data_type"],
        }
        if checked["supported"]:
            supported.append(entry)
        else:
            entry["source_version_id"] = withdrawn_version["version_id"]
            entry["conflict_reason"] = (
                f"字段类型冲突：条件登记时来源版本 {withdrawn_version['version_id']} "
                f"的字段类型为 {withdrawn_version['data_type']}，撤回恢复后类型为 "
                f"{restored_version['data_type']}，操作符 {cond['operator']} 不再受支持"
            )
            conflicting.append(entry)

    # 已确认绑定带来的原字段来源信息
    binding = None
    for row in store.rows(BINDING_TABLE):
        if row.get("tag_id") == tag["tag_id"] and row.get("status") == "confirmed":
            binding = dict(row)
            break

    return {
        "tag_id": tag["tag_id"],
        "tag_name": tag.get("tag_name", ""),
        "tag_status": tag.get("status", ""),
        "binding_id": binding["binding_id"] if binding else None,
        "point_in_time": binding["point_in_time"] if binding else None,
        "has_conflict": bool(conflicting),
        "conflicting_conditions": conflicting,
        "supported_conditions": supported,
    }


def list_withdraws(attr_id: str | None = None) -> list[dict[str, Any]]:
    rows = store.rows(WITHDRAW_TABLE)
    if attr_id is not None:
        rows = [r for r in rows if r.get("attr_id") == attr_id]
    return [dict(r) for r in rows]


def get_withdraw(withdraw_id: str) -> dict[str, Any] | None:
    return store.find_by_field(WITHDRAW_TABLE, "withdraw_id", withdraw_id)


# ========== 重新打开标签校验 ==========
def reopen_tag_validation(withdraw_id: str) -> dict[str, Any]:
    """基于撤回记录重新打开候选标签的校验，逐条说明冲突的字段类型。"""
    record = get_withdraw(withdraw_id)
    if record is None:
        raise ValueError(f"Withdraw not found: {withdraw_id}")

    candidates_report: list[dict[str, Any]] = []
    for candidate in record.get("review_candidates", []):
        tag = store.find_by_field("tag_define", "tag_id", candidate["tag_id"])
        current_version = get_current_version(record["attr_id"])
        condition_reports: list[dict[str, Any]] = []
        if tag is not None and current_version is not None:
            for cond in _attr_conditions_for(tag, record["attr_id"]):
                checked = check_condition(cond, current_version["data_type"])
                condition_reports.append(checked)
        still_conflicting = [c for c in condition_reports if not c["supported"]]
        resolved = [c for c in condition_reports if c["supported"]]
        candidates_report.append({
            "tag_id": candidate["tag_id"],
            "tag_name": candidate["tag_name"],
            "binding_id": candidate.get("binding_id"),
            "point_in_time": candidate.get("point_in_time"),
            "current_version_id": current_version["version_id"] if current_version else None,
            "current_data_type": current_version["data_type"] if current_version else None,
            "still_conflicting": still_conflicting,
            "resolved_conditions": resolved,
            "review_open": True,
        })

    return {
        "withdraw_id": withdraw_id,
        "attr_id": record["attr_id"],
        "restored_data_type": record["restored_data_type"],
        "candidates": candidates_report,
        "unaffected_tag_ids": [t["tag_id"] for t in record.get("unaffected_tags", [])],
    }


def validate_tag_at_point_in_time(tag_id: str, point_in_time: str) -> dict[str, Any]:
    """时点校验：按指定时点解析每个属性的唯一版本并检查条件兼容性。"""
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    if tag is None:
        raise ValueError(f"Tag not found: {tag_id}")

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    type_conflicts: list[dict[str, Any]] = []
    for attr_id in tag.get("source_attrs", []):
        if not attr_id.startswith("ATTR-"):
            continue
        version = get_version_at(attr_id, point_in_time)
        if version is None:
            missing.append(attr_id)
            continue
        for cond in _attr_conditions_for(tag, attr_id):
            checked = check_condition(cond, version["data_type"])
            checked["resolved_version_id"] = version["version_id"]
            resolved.append(checked)
            if not checked["supported"]:
                type_conflicts.append(checked)

    return {
        "tag_id": tag_id,
        "point_in_time": point_in_time,
        "valid": not missing and not type_conflicts,
        "missing_attrs": missing,
        "type_conflicts": type_conflicts,
        "resolved_conditions": resolved,
    }


def reset_versioning_state() -> None:
    """清空回滚相关三张表（测试隔离用）。"""
    store.rows(VERSION_TABLE).clear()
    store.rows(WITHDRAW_TABLE).clear()
    store.rows(BINDING_TABLE).clear()
