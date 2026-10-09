"""FastAPI 入口。"""
import logging
import os

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import accounts, account_summary, auth, batch, cloudflare, health, instances, network, oci_options, proxies, region_subscription, sniper, terminal
from app.api import settings as settings_api
from app.core.audit import AuditMiddleware
from app.core.config import settings
from app.core.deps import SessionLocal, engine, get_current_operator
from app.core.migrations import run_db_migrations
from app.core.security import hash_password
from app.models.models import Base, Operator
from app.services import tg_bot
from app.workers.scheduler import start_scheduler
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
    # 建表（新库一键建表，幂等）+ 启动自动跑 Alembic 迁移（老库加列/改结构，幂等）
    # 以后新增字段只需写迁移脚本，不再需要手动补 SQL
    Base.metadata.create_all(bind=engine)
    run_db_migrations(engine)
    logger.info("数据表初始化完成")
    _seed_admin()
    start_scheduler()
    await sniper_manager.start()
    await tg_bot.start()
    yield
    await tg_bot.stop()
    await sniper_manager.stop()
    logger.info("服务关闭")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# CORS：单端口部署后生产环境是同源的（/、/api、/docs 都在 8035），不需要 CORS；
# 保留此中间件仅供本地前后端分离开发（vite dev 在 5173、后端在 8000）
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
app.include_router(region_subscription.router, prefix="/api", tags=["区域订阅"], dependencies=auth_dep)
app.include_router(account_summary.router, prefix="/api/account-summary", tags=["账户摘要"], dependencies=auth_dep)
app.include_router(proxies.router, prefix="/api/proxies", tags=["代理管理"], dependencies=auth_dep)
app.include_router(health.router, prefix="/api/health", tags=["存活检查"], dependencies=auth_dep)
app.include_router(batch.router, prefix="/api/batch", tags=["批量任务"], dependencies=auth_dep)
app.include_router(network.router, prefix="/api/network", tags=["网络/换IP"], dependencies=auth_dep)
app.include_router(instances.router, prefix="/api/instances", tags=["实例运维"], dependencies=auth_dep)
app.include_router(cloudflare.router, prefix="/api/cloudflare", tags=["Cloudflare"], dependencies=auth_dep)
app.include_router(oci_options.router, prefix="/api/oci-options", tags=["OCI 选项查询"], dependencies=auth_dep)
app.include_router(sniper.router, prefix="/api/sniper", tags=["抢机任务"], dependencies=auth_dep)
app.include_router(terminal.router)
app.include_router(settings_api.router, prefix="/api/settings", tags=["系统设置"], dependencies=auth_dep)


@app.get("/api/ping")
def ping():
    return {"ok": True, "app": settings.APP_NAME}


# ============ 单端口部署：FastAPI 直接托管前端构建产物 ============
# 挂载顺序至关重要：必须放在所有 /api 路由（含 /docs、/openapi.json，FastAPI
# 在构造时已注册）之后。Starlette 按注册顺序匹配，未命中接口的请求才会落到
# 下面的静态资源与 SPA fallback，不会吞掉任何接口。
# 前端用相对路径 /api 调接口（见 frontend/src/api/client.js），同源无跨域问题。
# STATIC_DIR 可用环境变量覆盖（默认 /app/static，由 Dockerfile 第二阶段拷入）；
# 本地开发没有该目录时仅提供 API 服务（前端用 npm run dev 的 5173）。
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
_INDEX_HTML = os.path.join(STATIC_DIR, "index.html")

if os.path.isfile(_INDEX_HTML):
    _assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(_assets_dir):
        # /assets/* 精确前缀挂载：JS/CSS 等静态资源
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def _serve_index():
        """面板首页。"""
        return FileResponse(_INDEX_HTML)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """SPA fallback：前端路由（/sniper、/login 等）全部回 index.html 让前端接管。

        /api/*、/docs、/openapi.json 在前面已注册，按顺序优先匹配，到这里
        说明都没命中，不会吞掉接口。
        """
        # /api 前缀但前面没匹配上 → 明确返回 JSON 404，别回 HTML（方便调接口时排查）
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "接口不存在"})
        # 静态目录下真实存在的文件（如 favicon.ico）直接返回；normpath + 前缀
        # 校验防路径穿越（/.../.. 之类一律回 index.html）
        candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
        if (
            full_path
            and candidate.startswith(STATIC_DIR + os.sep)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(_INDEX_HTML)

    logger.info("已托管前端静态文件：%s", STATIC_DIR)
else:
    logger.warning("未找到前端构建产物 %s，仅提供 API 服务（本地开发请用 npm run dev）", _INDEX_HTML)
