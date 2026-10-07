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
        raise HTTPException(status_code=400, detail=str(e))
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
    client = build_client_for_account(account)
    try:
        result = await client.create_region_subscription(account.tenancy_ocid, region)
        logger.info("账号 %s 订阅新区域 %s 成功", account_id, region)
        return {"ok": True, "region": region, "result": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await client.aclose()


@router.get("/oci-regions")
async def list_oci_regions(db: Session = Depends(get_db)):
    """查 OCI 全部可用区域（供前端下拉；用第一个账号的凭证签名）。"""
    account = _first_account(db)
    client = build_client_for_account(account)
    try:
        regions = await client.list_all_regions()
        return [
            {"region_name": r.get("regionName", ""), "region_key": r.get("regionKey", "")}
            for r in regions
            if r.get("regionName")
        ]
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await client.aclose()
