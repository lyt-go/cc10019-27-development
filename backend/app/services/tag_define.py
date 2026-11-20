"""标签定义服务：管理标签规则的定义与元数据。"""
from __future__ import annotations

from typing import Any

from app.store import store


def list_tags() -> list[dict[str, Any]]:
    """列出所有标签定义。"""
    return store.rows("tag_define")


def get_tag(tag_id: str) -> dict[str, Any] | None:
    """获取单个标签定义。"""
    return store.find_by_field("tag_define", "tag_id", tag_id)


def create_tag(data: dict[str, Any]) -> dict[str, Any]:
    """创建标签定义。"""
    row = store.insert("tag_define", data)
    return row


def update_tag(tag_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """更新标签定义。"""
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    if tag is None:
        return None
    return store.update("tag_define", tag["id"], updates)


def delete_tag(tag_id: str) -> bool:
    """删除标签定义。"""
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    if tag is None:
        return False
    return store.delete("tag_define", tag["id"])


def get_tags_by_level(level: str) -> list[dict[str, Any]]:
    """按层级筛选标签。"""
    return store.find_all_by_field("tag_define", "tag_level", level)


def validate_tag_logic(tag_id: str) -> dict[str, Any]:
    """校验标签计算逻辑是否引用了有效的属性。"""
    tag = get_tag(tag_id)
    if tag is None:
        return {"valid": False, "error": "Tag not found"}

    source_attrs = tag.get("source_attrs", [])
    missing = []
    for attr_id in source_attrs:
        # 标签间引用（TAG-xxx）不属于属性字典校验范围
        if str(attr_id).startswith("TAG-"):
            continue
        attr = store.find_by_field("attr_dict", "attr_id", attr_id)
        if attr is None:
            missing.append(attr_id)

    return {
        "tag_id": tag_id,
        "valid": len(missing) == 0,
        "missing_attrs": missing,
        "source_attrs": source_attrs,
    }


def validate_tag_at_point_in_time(tag_id: str, point_in_time: str) -> dict[str, Any]:
    """时点校验：按指定时点解析属性字段版本，检查条件与字段类型是否兼容。

    同一时点每个属性只有一个生效版本，因此不会读到相互矛盾的字段类型。
    """
    from app.services.attr_version import validate_tag_at_point_in_time as _validate

    return _validate(tag_id, point_in_time)


def confirm_conditions(tag_id: str, point_in_time: str | None = None, remark: str = "") -> dict[str, Any]:
    """确认标签条件，锁定确认时点与原字段来源版本。"""
    from app.services.attr_version import confirm_tag_conditions

    return confirm_tag_conditions(tag_id, point_in_time=point_in_time, remark=remark)


def reopen_validation(withdraw_id: str) -> dict[str, Any]:
    """基于撤回记录重新打开标签校验，输出冲突字段类型说明。"""
    from app.services.attr_version import reopen_tag_validation

    return reopen_tag_validation(withdraw_id)
