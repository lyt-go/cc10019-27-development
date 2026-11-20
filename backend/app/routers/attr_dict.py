"""属性字典路由：管理用户属性字段的元数据定义、生效版本与撤回回滚。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.schemas import ActionPayload, ActionResult
from app.services import attr_version
from app.services.attr_dict import (
    list_attrs,
    get_attr,
    create_attr,
    update_attr,
    delete_attr,
    get_attrs_by_source,
)
from app.store import store, query_filters

router = APIRouter(prefix="/api/attr-dict", tags=["attr_dict"])


class PublishVersionPayload(BaseModel):
    """按生效时间写入属性字段版本。"""

    definition: dict[str, Any]
    effective_from: str
    change_type: str = "update"
    remark: str = ""


class WithdrawPayload(BaseModel):
    """撤回当前定义并回滚到目标历史版本。"""

    target_version_id: str
    effective_time: str | None = None
    remark: str = ""


class ConfirmBindingPayload(BaseModel):
    """确认标签条件时点。"""

    point_in_time: str | None = None
    remark: str = ""


# ========== 属性字段版本（具体路径优先于参数化 CRUD 路由注册） ==========
@router.get("/versions/{attr_id}")
def versions(attr_id: str) -> dict[str, Any]:
    """列出属性的全部历史版本。"""
    if get_attr(attr_id) is None and not attr_version.list_versions(attr_id):
        raise HTTPException(status_code=404, detail="Attr not found")
    rows = attr_version.list_versions(attr_id)
    return {"items": rows, "total": len(rows)}


@router.post("/versions/{attr_id}/publish")
def publish_version(attr_id: str, payload: PublishVersionPayload) -> ActionResult:
    """按生效时间写入一条新的属性字段定义版本。"""
    try:
        row = attr_version.publish_version(
            attr_id=attr_id,
            definition=payload.definition,
            effective_from=payload.effective_from,
            change_type=payload.change_type,
            remark=payload.remark,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ActionResult(ok=True, message="Version published", entry=row)


@router.get("/versions/{attr_id}/as-of")
def version_as_of(attr_id: str, point_in_time: str) -> dict[str, Any]:
    """时点查询：返回该时点唯一生效的属性版本。"""
    row = attr_version.get_version_at(attr_id, point_in_time)
    if row is None:
        raise HTTPException(status_code=404, detail="No effective version at the given time")
    return row


@router.get("/versions/{attr_id}/periods")
def version_periods(attr_id: str, period_grain: str, start: str, end: str) -> dict[str, Any]:
    """按周期回查各周期可用的属性字段版本。"""
    try:
        return attr_version.get_versions_by_period(attr_id, period_grain, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ========== 撤回（回滚）记录 ==========
@router.get("/withdraws")
def withdraw_list(attr_id: str | None = None) -> dict[str, Any]:
    """列出撤回记录，可按属性过滤。"""
    rows = attr_version.list_withdraws(attr_id)
    return {"items": rows, "total": len(rows)}


@router.post("/withdraws/{attr_id}")
def withdraw(attr_id: str, payload: WithdrawPayload) -> ActionResult:
    """撤回当前字段定义并回滚到目标版本，生成撤回记录与复核候选。"""
    try:
        record = attr_version.withdraw_attr(
            attr_id=attr_id,
            target_version_id=payload.target_version_id,
            effective_time=payload.effective_time,
            remark=payload.remark,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ActionResult(ok=True, message="Withdrawn", entry=record)


@router.get("/withdraws/{withdraw_id}")
def withdraw_detail(withdraw_id: str) -> dict[str, Any]:
    """查看单条撤回记录（撤回区间与复核候选分开留痕）。"""
    record = attr_version.get_withdraw(withdraw_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Withdraw not found")
    return record


@router.post("/withdraws/{withdraw_id}/reopen-validation")
def reopen_validation(withdraw_id: str) -> ActionResult:
    """重新打开候选标签的校验，返回冲突字段类型说明。"""
    try:
        report = attr_version.reopen_tag_validation(withdraw_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ActionResult(ok=True, message="Validation reopened", entry=report)


# ========== 标签条件绑定（确认时点与原字段来源） ==========
@router.get("/bindings")
def bindings(tag_id: str | None = None) -> dict[str, Any]:
    rows = attr_version.list_bindings(tag_id)
    return {"items": rows, "total": len(rows)}


@router.post("/bindings/{tag_id}/confirm")
def confirm_binding(tag_id: str, payload: ConfirmBindingPayload) -> ActionResult:
    """确认标签条件，锁定确认时点与原字段来源版本。"""
    try:
        row = attr_version.confirm_tag_conditions(
            tag_id, point_in_time=payload.point_in_time, remark=payload.remark
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ActionResult(ok=True, message="Conditions confirmed", entry=row)


# ========== 常规 CRUD（保持原始路径契约） ==========
@router.get("")
def index(request: Request, page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("attr_dict")
    # query contract: filters=query_filters(request)
    filters = query_filters(request)
    if filters:
        rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in filters.items())]
    total = len(rows)
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": total, "page": page, "size": size}


@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("attr_dict", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("")
def create(payload: dict[str, Any]) -> ActionResult:
    row = create_attr(payload)
    return ActionResult(ok=True, message="Created", entry=row)


@router.put("/{entry_id}")
def update(entry_id: int, payload: dict[str, Any]) -> ActionResult:
    row = store.find("attr_dict", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    updated = update_attr(row["attr_id"], payload)
    return ActionResult(ok=True, message="Updated", entry=updated)


@router.delete("/{entry_id}")
def remove(entry_id: int) -> ActionResult:
    row = store.find("attr_dict", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    delete_attr(row["attr_id"])
    return ActionResult(ok=True, message="Deleted")


@router.get("/by-source/{source}")
def by_source(source: str) -> list[dict[str, Any]]:
    return get_attrs_by_source(source)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("attr_dict", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
