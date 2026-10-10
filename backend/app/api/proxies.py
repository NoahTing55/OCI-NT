"""代理管理：CRUD。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import encrypt_text
from app.models.models import Proxy
from app.schemas.schemas import ProxyCreate, ProxyOut

router = APIRouter()

@router.get("", response_model=list[ProxyOut])
def list_proxies(db: Session = Depends(get_db)):
    result = []
    for p in db.query(Proxy).order_by(Proxy.id.desc()).all():
        out = ProxyOut.model_validate(p)
        # 透出绑定状态，供新建账号时默认选中未使用代理
        out.bound_account_name = p.account.name if p.account is not None else None
        result.append(out)
    return result

@router.post("", response_model=ProxyOut)
def create_proxy(data: ProxyCreate, db: Session = Depends(get_db)):
    if data.scheme not in ("http", "socks5"):
        raise HTTPException(status_code=400, detail="scheme 只能是 http 或 socks5")
    try:
        password_enc = encrypt_text(data.password) if data.password else ""
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    proxy = Proxy(
        name=data.name,
        scheme=data.scheme,
        host=data.host.strip(),
        port=data.port,
        username=data.username,
        password_enc=password_enc,
        remark=data.remark,
    )
    db.add(proxy)
    db.commit()
    db.refresh(proxy)
    # TODO(M2)：创建后自动做一次连通性 + 延迟测试，写入 status / latency_ms
    return ProxyOut.model_validate(proxy)

@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, db: Session = Depends(get_db)):
    proxy = db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="代理不存在")
    if proxy.account is not None:
        raise HTTPException(
            status_code=400,
            detail=f"该代理正被账号「{proxy.account.name}」绑定，请先解绑再删除",
        )
    name = proxy.name
    db.delete(proxy)
    db.commit()
    return {"ok": True}

@router.post("/test-all")
async def test_all_proxies():
    """手动触发全部代理测速（后台运行，立即返回）。"""
    import asyncio
    from app.workers.scheduler import _job_proxy_speedtest
    asyncio.create_task(_job_proxy_speedtest())
    return {"ok": True, "message": "代理测速已开始，请稍后刷新查看结果"}
