"""实例运维：多账号聚合列表 + 编辑实例。

- GET /api/instances：并发查多个账号的实例并补全公网/私网 IP，
  支持按账号 / 区域 / 状态筛选；单个账号失败不影响其他账号（记入 errors）。
- PUT /api/instances/{account_id}/{instance_id}：只允许改 display_name /
  freeform_tags / metadata；shape、OCPU、内存等字段直接拒绝并返回明确中文错误。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.oci_factory import build_client_for_account
from app.models.models import Account
from app.schemas.schemas import InstanceEditIn, InstanceListOut
from app.services import instances as instance_service

router = APIRouter()

# 明确拒绝在线变更的字段（OCI 不支持，需重建实例）
FORBIDDEN_EDIT_FIELDS = {"shape", "ocpus", "memory_gb", "memory_in_gbs"}

@router.get("", response_model=InstanceListOut)
async def list_instances(
    account_id: int | None = Query(default=None, description="按账号筛选"),
    region: str | None = Query(default=None, description="按区域筛选"),
    state: str | None = Query(default=None, description="按状态筛选，如 RUNNING / STOPPED"),
    db: Session = Depends(get_db),
):
    """多账号多区域聚合查询实例。"""
    q = db.query(Account)
    if account_id:
        q = q.filter(Account.id == account_id)
    if region:
        q = q.filter(Account.region == region)
    accounts = q.order_by(Account.id).all()
    # 带 Redis 缓存的聚合查询（TTL 可配；Redis 不可用自动降级直查）
    items, errors = await instance_service.fetch_all_instances_cached(
        accounts, {"account_id": account_id, "region": region, "state": state}
    )
    if state:
        items = [i for i in items if (i.get("lifecycle_state") or "") == state]
    return {"items": items, "errors": errors}

@router.put("/{account_id}/{instance_id}")
async def edit_instance(
    account_id: int, instance_id: str, data: InstanceEditIn, db: Session = Depends(get_db)
):
    """编辑实例。允许：display_name、freeform_tags、metadata；其余拒绝。"""
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 拦截 shape / OCPU / 内存等不支持在线变更的字段，给明确中文错误
    extra = data.model_extra or {}
    hit = FORBIDDEN_EDIT_FIELDS & set(extra.keys())
    if hit:
        raise HTTPException(
            status_code=400,
            detail=f"不支持在线变更：{', '.join(sorted(hit))}。如需升配/换机型请走重建实例流程",
        )

    details: dict = {}
    if data.display_name is not None:
        details["displayName"] = data.display_name
    if data.freeform_tags is not None:
        details["freeformTags"] = data.freeform_tags
    if data.metadata is not None:
        details["metadata"] = data.metadata
    if not details:
        raise HTTPException(status_code=400, detail="没有可更新的字段（可改：名称、标签、metadata）")

    client = build_client_for_account(account)
    try:
        resp = await client.update_instance(instance_id, details)
    finally:
        await client.aclose()
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"OCI 更新失败：HTTP {resp.status_code} {resp.text[:200]}"
        )
    # 编辑成功后失效实例列表缓存
    instance_service.invalidate_instance_cache()
    return {"ok": True, "instance": resp.json()}
