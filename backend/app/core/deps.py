"""FastAPI 依赖：DB 会话 + 登录鉴权。"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import decode_access_token

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_operator(request: Request, db: Session = Depends(get_db)):
    """从 Authorization: Bearer <JWT> 解析当前登录账号。

    未登录 / Token 过期伪造 / 账号被禁用 → 401。公开接口（登录/初始化/ping）
    不挂这个依赖。
    """
    # 延迟导入 models，避免 models ↔ deps 潜在的循环导入
    from app.models.models import Operator

    username = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        username = decode_access_token(auth[7:].strip())
    op = None
    if username:
        op = (
            db.query(Operator)
            .filter(Operator.username == username, Operator.is_active.is_(True))
            .first()
        )
    if not op:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return op
