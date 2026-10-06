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
    results = await check_all_accounts(db)
    # TG 汇总通知（只在手动触发时发送）
    try:
        from app.core import telegram
        ok = sum(1 for r in results if r.get("status") == "healthy")
        fail = len(results) - ok
        lines = ["🔍 存活检查完成：共 %d 个账号，正常 %d，异常 %d" % (len(results), ok, fail)]
        for r in results:
            mark = "✅" if r.get("status") == "healthy" else "❌"
            typ = {"free": "免费", "paid": "付费"}.get(r.get("account_type") or "", "")
            lines.append("%s %s（%s）%s" % (mark, r.get("name"), r.get("region"), f"·{typ}" if typ else ""))
        await telegram.send_message("\n".join(lines))
    except Exception:
        pass
    return results
