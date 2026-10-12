"""实例运维：多账号聚合列表 + 编辑实例。

- GET /api/instances：并发查多个账号的实例并补全公网/私网 IP，
  支持按账号 / 区域 / 状态筛选；单个账号失败不影响其他账号（记入 errors）。
- PUT /api/instances/{account_id}/{instance_id}：可改 display_name /
  freeform_tags / metadata；OCPU/内存仅 Flex 机型且关机后可改；
  启动卷支持在线扩容（只能调大）。
- GET /api/instances/{account_id}/{instance_id}/boot-volume：查启动卷当前大小（编辑对话框回填用）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.oci_factory import build_client_for_account
from app.models.models import Account
from app.schemas.schemas import InstanceEditIn, InstanceListOut
from app.services import instances as instance_service

router = APIRouter()

@router.get("", response_model=InstanceListOut)
async def list_instances(
    account_id: int | None = Query(default=None, description="按账号筛选"),
    region: str | None = Query(default=None, description="按区域筛选"),
    state: str | None = Query(default=None, description="按状态筛选，如 RUNNING / STOPPED"),
    force: bool = Query(default=False, description="强制刷新：先失效缓存再查 OCI"),
    db: Session = Depends(get_db),
):
    """多账号多区域聚合查询实例。"""
    if force:
        # 手动点"刷新"时绕开缓存，保证看到 OCI 实时数据
        instance_service.invalidate_instance_cache()
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

@router.get("/{account_id}/{instance_id}/boot-volume")
async def get_boot_volume_info(
    account_id: int, instance_id: str, db: Session = Depends(get_db)
):
    """查实例启动卷当前大小（编辑对话框回填硬盘当前值用）。"""
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    client = build_client_for_account(account)
    try:
        bv_id, size = await _resolve_boot_volume(client, instance_id)
    finally:
        await client.aclose()
    return {"boot_volume_id": bv_id, "size_gb": size}


async def _resolve_boot_volume(client, instance_id: str) -> tuple[str, float]:
    """查实例的启动卷 ID 和当前大小。失败抛 HTTPException（中文）。"""
    # 先拿实例的 compartment（查挂载关系需要）
    resp = await client.get_instance(instance_id)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"查询实例失败：HTTP {resp.status_code} {resp.text[:200]}"
        )
    compartment_id = resp.json().get("compartmentId", "")
    resp = await client.list_boot_volume_attachments(instance_id, compartment_id)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"查询启动卷挂载失败：HTTP {resp.status_code} {resp.text[:200]}"
        )
    items = resp.json()
    if not items:
        raise HTTPException(status_code=502, detail="未找到该实例的启动卷")
    bv_id = items[0].get("bootVolumeId", "")
    resp = await client.get_boot_volume(bv_id)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"查询启动卷失败：HTTP {resp.status_code} {resp.text[:200]}"
        )
    size = resp.json().get("sizeInGBs")
    return bv_id, size


@router.put("/{account_id}/{instance_id}")
async def edit_instance(
    account_id: int, instance_id: str, data: InstanceEditIn, db: Session = Depends(get_db)
):
    """编辑实例。

    - display_name / freeform_tags / metadata：直接更新；
    - ocpus / memory_in_gbs：仅 Flex 机型、实例需先关机（STOPPED），走 shapeConfig 更新；
    - boot_volume_size_gb：启动卷扩容，只能调大不能调小。
    """
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    details: dict = {}
    if data.display_name is not None:
        details["displayName"] = data.display_name
    if data.freeform_tags is not None:
        details["freeformTags"] = data.freeform_tags
    if data.metadata is not None:
        details["metadata"] = data.metadata

    want_shape = data.ocpus is not None or data.memory_in_gbs is not None
    want_disk = data.boot_volume_size_gb is not None
    if not details and not want_shape and not want_disk:
        raise HTTPException(status_code=400, detail="没有可更新的字段")

    client = build_client_for_account(account)
    try:
        # ---- 基础字段 ----
        if details:
            resp = await client.update_instance(instance_id, details)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"OCI 更新失败：HTTP {resp.status_code} {resp.text[:200]}",
                )

        # ---- OCPU / 内存（Flex 机型 + 关机状态） ----
        if want_shape:
            resp = await client.get_instance(instance_id)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"查询实例失败：HTTP {resp.status_code} {resp.text[:200]}",
                )
            inst = resp.json()
            shape = inst.get("shape", "")
            if "Flex" not in shape:
                raise HTTPException(
                    status_code=400,
                    detail=f"当前机型 {shape} 不支持在线调整 CPU/内存（仅 Flex 机型支持）",
                )
            if (inst.get("lifecycleState") or "") != "STOPPED":
                raise HTTPException(
                    status_code=400, detail="请先关机（STOPPED）后再修改 CPU/内存"
                )
            cur_cfg = inst.get("shapeConfig") or {}
            shape_cfg: dict = {}
            if data.ocpus is not None:
                if data.ocpus <= 0:
                    raise HTTPException(status_code=400, detail="OCPU 必须大于 0")
                shape_cfg["ocpus"] = data.ocpus
            else:
                shape_cfg["ocpus"] = cur_cfg.get("ocpus")
            if data.memory_in_gbs is not None:
                if data.memory_in_gbs <= 0:
                    raise HTTPException(status_code=400, detail="内存必须大于 0")
                shape_cfg["memoryInGBs"] = data.memory_in_gbs
            else:
                shape_cfg["memoryInGBs"] = cur_cfg.get("memoryInGBs")
            resp = await client.update_instance(instance_id, {"shapeConfig": shape_cfg})
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"OCI 更新 CPU/内存失败：HTTP {resp.status_code} {resp.text[:200]}",
                )

        # ---- 启动卷扩容 ----
        if want_disk:
            new_size = data.boot_volume_size_gb
            bv_id, cur_size = await _resolve_boot_volume(client, instance_id)
            if new_size <= cur_size:
                raise HTTPException(
                    status_code=400,
                    detail=f"硬盘只能扩容：新大小 {new_size} GB 必须大于当前 {cur_size} GB",
                )
            resp = await client.update_boot_volume(bv_id, new_size)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"OCI 硬盘扩容失败：HTTP {resp.status_code} {resp.text[:200]}",
                )
    finally:
        await client.aclose()

    # 编辑成功后失效实例列表缓存
    instance_service.invalidate_instance_cache()
    return {"ok": True}
