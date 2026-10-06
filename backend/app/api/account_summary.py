"""账户摘要：每个账号的实例统计 + compute 配额。

- 实例数据复用实例聚合接口的 Redis 缓存（fetch_all_instances_cached），
  不每次调 OCI；OCPU/内存从 shapeConfig 汇总（只计 RUNNING 实例）。
- 配额实时调 Limits API（compute 服务，limit_name 按 E2/A1/E5 映射），
  单个失败返回 null 不影响整体；多账号并发查询。
- 只读 GET 接口，不记审计日志（审计中间件只记录写操作）。
"""
import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.oci_factory import build_client_for_account
from app.models.models import Account
from app.services import instances as instance_service

logger = logging.getLogger(__name__)

router = APIRouter()

# 配额 limit_name 映射（参照 OCI-Start OciLimitsUtils）
QUOTA_LIMITS = {
    "e2": "standard-e2-core-count",   # E2 Micro 等
    "a1": "standard-a1-core-count",   # A1 Flex ARM
    "e5": "standard-e5-core-count",   # E5 Flex
}

QUOTA_LABELS = {"e2": "E2", "a1": "ARM A1", "e5": "E5"}


async def _fetch_quotas(account: Account) -> dict:
    """查单个账号的三项 compute 配额。失败的项返回 None 值。"""
    client = build_client_for_account(account, timeout=15.0)
    try:
        compartment = account.tenancy_ocid
        results = await asyncio.gather(
            *[client.get_compute_quota(compartment, name) for name in QUOTA_LIMITS.values()],
            return_exceptions=True,
        )
        quotas = {}
        for (key, _), res in zip(QUOTA_LIMITS.items(), results):
            if isinstance(res, Exception):
                logger.warning("账号「%s」配额 %s 查询异常：%s", account.name, key, str(res)[:120])
                quotas[key] = {"available": None, "used": None}
            else:
                quotas[key] = res
        return quotas
    finally:
        await client.aclose()


@router.get("")
async def account_summary(db: Session = Depends(get_db)):
    """返回每个账号的摘要：实例数/运行数、OCPU/内存已用、E2/A1/E5 配额。"""
    accounts = db.query(Account).order_by(Account.id).all()
    if not accounts:
        return []

    # 实例走缓存聚合（与实例运维页同一份数据）
    items, _errors = await instance_service.fetch_all_instances_cached(accounts, {})

    # 按账号分组统计（只计 RUNNING 的 OCPU/内存）
    stats: dict[int, dict] = {}
    for a in accounts:
        stats[a.id] = {"instance_count": 0, "running_count": 0, "ocpu_used": 0.0, "memory_used_gb": 0.0}
    for it in items:
        aid = it.get("account_id")
        if aid not in stats:
            continue
        st = stats[aid]
        st["instance_count"] += 1
        if (it.get("lifecycle_state") or "") == "RUNNING":
            st["running_count"] += 1
            sc = it.get("shape_config") or {}
            try:
                st["ocpu_used"] += float(sc.get("ocpus") or 0)
                st["memory_used_gb"] += float(sc.get("memoryInGBs") or 0)
            except (TypeError, ValueError):
                pass

    # 并发查各账号配额
    quotas_list = await asyncio.gather(*[_fetch_quotas(a) for a in accounts])

    result = []
    for account, quotas in zip(accounts, quotas_list):
        st = stats[account.id]
        result.append(
            {
                "account_id": account.id,
                "name": account.name,
                "region": account.region,
                "instance_count": st["instance_count"],
                "running_count": st["running_count"],
                "ocpu_used": round(st["ocpu_used"], 2),
                "memory_used_gb": round(st["memory_used_gb"], 2),
                "quotas": quotas,
            }
        )
    return result
