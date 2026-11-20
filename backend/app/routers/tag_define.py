"""标签定义路由：管理标签规则的定义、依赖校验与时点复核。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.schemas import ActionPayload, ActionResult
from app.services import attr_version
from app.services.tag_define import (
    list_tags, get_tag, create_tag, update_tag, delete_tag, get_tags_by_level,
    validate_tag_logic, validate_tag_at_point_in_time, confirm_conditions,
    reopen_validation,
)
from app.store import store, query_filters

router = APIRouter(prefix="/api/tag-define", tags=["tag_define"])


class ConfirmPayload(BaseModel):
    """确认标签条件时点。"""

    point_in_time: str | None = None
    remark: str = ""


class ReopenPayload(BaseModel):
    """按撤回记录重新打开校验。"""

    withdraw_id: str


# ========== 时点校验与条件复核（具体路径优先注册） ==========
@router.get("/bindings")
def tag_bindings(tag_id: str | None = None) -> dict[str, Any]:
    rows = attr_version.list_bindings(tag_id)
    return {"items": rows, "total": len(rows)}


@router.post("/reopen-validation")
def reopen(payload: ReopenPayload) -> ActionResult:
    """按撤回记录重新打开候选标签校验。"""
    report = reopen_validation(payload.withdraw_id)
    return ActionResult(ok=True, message="Validation reopened", entry=report)


@router.get("/{tag_id}/validate-at")
def validate_at(tag_id: str, point_in_time: str) -> dict[str, Any]:
    """时点校验：按指定时点解析属性版本并检查字段类型冲突。"""
    try:
        return validate_tag_at_point_in_time(tag_id, point_in_time)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{tag_id}/confirm-conditions")
def confirm_tag(tag_id: str, payload: ConfirmPayload) -> ActionResult:
    """确认标签条件，锁定确认时点与原字段来源。"""
    try:
        row = confirm_conditions(tag_id, point_in_time=payload.point_in_time, remark=payload.remark)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ActionResult(ok=True, message="Conditions confirmed", entry=row)


# ========== 常规 CRUD（保持原始路径契约） ==========
@router.get("")
def index(request: Request, page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("tag_define")
    # query contract: filters=query_filters(request)
    filters = query_filters(request)
    if filters:
        rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in filters.items())]
    total = len(rows)
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": total, "page": page, "size": size}


@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("tag_define", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("")
def create(payload: dict[str, Any]) -> ActionResult:
    row = create_tag(payload)
    return ActionResult(ok=True, message="Created", entry=row)


@router.put("/{entry_id}")
def update(entry_id: int, payload: dict[str, Any]) -> ActionResult:
    row = store.find("tag_define", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    updated = update_tag(row["tag_id"], payload)
    return ActionResult(ok=True, message="Updated", entry=updated)


@router.delete("/{entry_id}")
def remove(entry_id: int) -> ActionResult:
    row = store.find("tag_define", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    delete_tag(row["tag_id"])
    return ActionResult(ok=True, message="Deleted")


@router.get("/by-level/{level}")
def by_level(level: str) -> list[dict[str, Any]]:
    return get_tags_by_level(level)


@router.get("/{tag_id}/validate")
def validate(tag_id: str) -> dict[str, Any]:
    return validate_tag_logic(tag_id)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("tag_define", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
