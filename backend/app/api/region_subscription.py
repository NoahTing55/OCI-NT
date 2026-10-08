"""区域订阅：升级账户查看/订阅新区域（免费账户无此能力）。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.oci_factory import build_client_for_account
from app.models.models import Account

logger = logging.getLogger(__name__)

router = APIRouter()


class SubscribeIn(BaseModel):
    region: str


class BatchSubscribeIn(BaseModel):
    """批量订阅区域请求体：多个账号订阅同一个新区域。"""
    account_ids: list[int]
    region_name: str


async def _do_subscribe_account_region(account: Account, region: str) -> tuple[bool, str]:
    """给单个账号订阅区域的核心逻辑（单订阅与批量订阅共用）。

    返回 (成功与否, 错误信息)：成功时错误信息为空字符串。
    """
    client = build_client_for_account(account)
    try:
        await client.create_region_subscription(account.tenancy_ocid, region)
        logger.info("账号 %s 订阅新区域 %s 成功", account.id, region)
        return True, ""
    except RuntimeError as e:
        logger.warning("账号 %s 订阅区域 %s 失败：%s", account.id, region, str(e)[:200])
        return False, str(e)
    finally:
        await client.aclose()


def _get_account(db: Session, account_id: int) -> Account:
    """取账号，不存在时 404 中文提示（免费/升级账户均可订阅区域）。"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


def _first_account(db: Session) -> Account:
    """取第一个账号（给 /oci-regions 这种不需要指定账号的接口用）。"""
    account = db.query(Account).order_by(Account.id).first()
    if not account:
        raise HTTPException(status_code=400, detail="还没有账号，无法查询区域列表")
    return account


@router.get("/accounts/{account_id}/region-subscriptions")
async def list_region_subscriptions(account_id: int, db: Session = Depends(get_db)):
    """查指定账号已订阅区域列表。"""
    account = _get_account(db, account_id)
    client = build_client_for_account(account)
    try:
        subs = await client.list_region_subscriptions(account.tenancy_ocid)
        return [
            {
                "region_name": s.get("regionName", ""),
                "status": s.get("status", ""),
                "is_home_region": bool(s.get("isHomeRegion", False)),
            }
            for s in subs
        ]
    except RuntimeError as e:
        msg = str(e)
        # 订阅接口 404/403（免费账户、无额外订阅或权限问题）时返回空列表，
        # 前端用账号主区域兜底显示，不报错
        if "404" in msg or "403" in msg or "NotAuthorizedOrNotFound" in msg:
            return []
        raise HTTPException(status_code=400, detail=msg)
    finally:
        await client.aclose()


@router.post("/accounts/{account_id}/region-subscriptions")
async def subscribe_region(
    account_id: int, data: SubscribeIn, db: Session = Depends(get_db)
):
    """给升级账户订阅新区域。"""
    account = _get_account(db, account_id)
    region = (data.region or "").strip()
    if not region:
        raise HTTPException(status_code=400, detail="区域不能为空")
    ok, err = await _do_subscribe_account_region(account, region)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "region": region}


@router.post("/region-subscriptions/batch-subscribe")
async def batch_subscribe_regions(
    data: BatchSubscribeIn, db: Session = Depends(get_db)
):
    """批量订阅区域：多个账号一次订阅同一个新区域（扩区）。

    请求体：{"account_ids": [1,2,3], "region_name": "us-ashburn-1"}。
    每个账号独立调用订阅接口，互不影响；返回每个账号的成功/失败明细。
    """
    region = (data.region_name or "").strip()
    if not region:
        raise HTTPException(status_code=400, detail="区域不能为空")
    if not data.account_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个账号")

    results = []
    for account_id in data.account_ids:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            results.append({
                "account_id": account_id,
                "account_name": f"账号 #{account_id}",
                "success": False,
                "error": "账号不存在",
            })
            continue
        ok, err = await _do_subscribe_account_region(account, region)
        results.append({
            "account_id": account.id,
            "account_name": account.name or f"账号 #{account.id}",
            "success": ok,
            "error": err,
        })
    success_count = sum(1 for r in results if r["success"])
    logger.info("批量订阅区域 %s：共 %d 个账号，成功 %d 个",
                region, len(results), success_count)
    return {"region": region, "results": results}


# OCI 公共区域硬编码回退（API 失败时用）
FALLBACK_OCI_REGIONS = [
    "ap-chuncheon-1", "ap-hyderabad-1", "ap-melbourne-1", "ap-mumbai-1",
    "ap-osaka-1", "ap-seoul-1", "ap-singapore-1", "ap-sydney-1", "ap-tokyo-1",
    "ca-montreal-1", "ca-toronto-1",
    "eu-amsterdam-1", "eu-frankfurt-1", "eu-madrid-1", "eu-milan-1",
    "eu-paris-1", "eu-stockholm-1", "eu-zurich-1",
    "me-abudhabi-1", "me-dubai-1", "me-jeddah-1",
    "mx-monterrey-1",
    "sa-saopaulo-1", "sa-santiago-1", "sa-vinhedo-1",
    "uk-cardiff-1", "uk-london-1",
    "us-ashburn-1", "us-chicago-1", "us-phoenix-1", "us-sanjose-1",
]


@router.get("/oci-regions")
async def list_oci_regions(db: Session = Depends(get_db)):
    """查 OCI 全部可用区域（供前端下拉；用第一个账号的凭证签名，失败时用硬编码回退）。"""
    account = _first_account(db)
    client = build_client_for_account(account)
    try:
        regions = await client.list_all_regions()
        result = [
            {"region_name": r.get("regionName", ""), "region_key": r.get("regionKey", "")}
            for r in regions
            if r.get("regionName")
        ]
        if result:
            return result
    except RuntimeError:
        pass
    finally:
        await client.aclose()
    # API 失败或空结果时用硬编码回退
    return [{"region_name": r, "region_key": r} for r in FALLBACK_OCI_REGIONS]
