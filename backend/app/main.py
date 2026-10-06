"""FastAPI 入口。"""
import logging

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, auth, batch, batch_create, cloudflare, health, instances, network, proxies, sniper
from app.core.audit import AuditMiddleware
from app.core.config import settings
from app.core.deps import SessionLocal, engine, get_current_operator
from app.core.security import hash_password
from app.models.models import Base, Operator
from app.workers.scheduler import start_scheduler
from app.workers.batch_create import batch_create_manager
from app.workers.sniper import sniper_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _seed_admin():
    """首次启动时用环境变量创建初始管理员（表为空且配了 ADMIN_USERNAME/ADMIN_PASSWORD 才执行）。"""
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        return
    db = SessionLocal()
    try:
        if db.query(Operator).count() == 0:
            db.add(
                Operator(
                    username=settings.ADMIN_USERNAME.strip(),
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                )
            )
            db.commit()
            logger.info("已从环境变量创建初始管理员账号（请登录后立即改密码）")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表（Alembic 迁移与 create_all 兼容，幂等）
    Base.metadata.create_all(bind=engine)
    logger.info("数据表初始化完成")
    _seed_admin()
    start_scheduler()
    await sniper_manager.start()
    await batch_create_manager.start()
    yield
    await batch_create_manager.stop()
    await sniper_manager.stop()
    logger.info("服务关闭")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 审计中间件：自动记录 POST/PUT/DELETE/PATCH（白名单见 core/audit.py）
app.add_middleware(AuditMiddleware)

# 登录鉴权：auth 路由公开；其余全部要求 Bearer JWT
auth_dep = [Depends(get_current_operator)]

app.include_router(auth.router, prefix="/api/auth", tags=["登录鉴权"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["账号管理"], dependencies=auth_dep)
app.include_router(proxies.router, prefix="/api/proxies", tags=["代理管理"], dependencies=auth_dep)
app.include_router(health.router, prefix="/api/health", tags=["存活检查"], dependencies=auth_dep)
app.include_router(batch.router, prefix="/api/batch", tags=["批量任务"], dependencies=auth_dep)
app.include_router(network.router, prefix="/api/network", tags=["网络/换IP"], dependencies=auth_dep)
app.include_router(instances.router, prefix="/api/instances", tags=["实例运维"], dependencies=auth_dep)
app.include_router(cloudflare.router, prefix="/api/cloudflare", tags=["Cloudflare"], dependencies=auth_dep)
app.include_router(sniper.router, prefix="/api/sniper", tags=["抢机任务"], dependencies=auth_dep)
app.include_router(batch_create.router, prefix="/api/batch-create", tags=["批量创建实例"], dependencies=auth_dep)


@app.get("/api/ping")
def ping():
    return {"ok": True, "app": settings.APP_NAME}
