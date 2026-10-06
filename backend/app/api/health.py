"""存活检查：一键检查单个 / 全部账号。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.schemas import CheckResult
from app.services.liveness import check_account_liveness, check_all_accounts

router = APIRouter()


@router.post("/check/{account_id}", response_model=CheckResult)
async def check_one(account_id: int, db: Session = Depends(get_db)):
    return await check_account_liveness(db, account_id)


@router.post("/check-all", response_model=list[CheckResult])
async def check_all(db: Session = Depends(get_db)):
    return await check_all_accounts(db)
