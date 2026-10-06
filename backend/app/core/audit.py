"""操作审计。

- AuditMiddleware：自动记录所有 POST/PUT/DELETE/PATCH 请求（白名单除外）。
  操作人从 JWT 取，无 JWT 则记 anonymous；请求体中的敏感字段
  （private_key/password/token/secret/totp 类）一律脱敏为 ***，绝不入库明文。
- log_operation：后台 worker（Celery 任务、抢机引擎等，非 HTTP 请求）手动调用。
  路由层不再手动调用，统一走中间件（action 命名沿用原有规范，见 ACTION_MAP）。
"""
import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.deps import SessionLocal
from app.models.models import OperationLog

logger = logging.getLogger(__name__)

# 需要审计的 HTTP 方法
AUDIT_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# 不审计的路径前缀
SKIP_PREFIXES = ("/api/ping", "/docs", "/openapi.json", "/redoc")

# 完全跳过：登录接口（含密码，即使脱敏也不入库）
SKIP_EXACT = {("POST", "/api/auth/login")}

# 记 action 但不记任何参数摘要（密码/验证码相关）
NO_DETAIL_PREFIXES = ("/api/auth/",)

# 敏感字段：key 包含以下子串（不区分大小写）即脱敏
SENSITIVE_SUBSTRS = (
    "private_key", "password", "passwd", "pwd", "token", "secret",
    "totp", "passphrase", "api_key", "apikey", "credential",
)

# (method, 路由 path 模板) -> action（沿用原有手动记录的命名规范）
ACTION_MAP = {
    ("POST", "/api/accounts"): "account.create",
    ("PUT", "/api/accounts/{account_id}"): "account.update",
    ("DELETE", "/api/accounts/{account_id}"): "account.delete",
    ("POST", "/api/accounts/{account_id}/bind-proxy"): "account.bind_proxy",
    ("POST", "/api/proxies"): "proxy.create",
    ("DELETE", "/api/proxies/{proxy_id}"): "proxy.delete",
    ("POST", "/api/health/check/{account_id}"): "health.check",
    ("POST", "/api/health/check-all"): "health.check_all",
    ("POST", "/api/batch"): "batch.create",
    ("PUT", "/api/instances/{account_id}/{instance_id}"): "instance.edit",
    ("POST", "/api/network/change-ip"): "network.change_ip",
    ("POST", "/api/network/ephemeral-ip/probe"): "network.probe",
    ("POST", "/api/cloudflare/tokens"): "cf.token_create",
    ("DELETE", "/api/cloudflare/tokens/{token_id}"): "cf.token_delete",
    ("POST", "/api/cloudflare/bindings"): "cf.bind_create",
    ("DELETE", "/api/cloudflare/bindings/{binding_id}"): "cf.bind_delete",
    ("POST", "/api/cloudflare/bindings/{binding_id}/sync"): "cf.sync",
    ("POST", "/api/cloudflare/sync-all"): "cf.sync_all",
    ("POST", "/api/sniper"): "snipe.create",
    ("POST", "/api/sniper/{task_id}/start"): "snipe.start",
    ("POST", "/api/sniper/{task_id}/pause"): "snipe.pause",
    ("DELETE", "/api/sniper/{task_id}"): "snipe.delete",
    ("PUT", "/api/settings"): "settings.update",
    ("POST", "/api/settings/test-telegram"): "settings.test_telegram",
    ("POST", "/api/auth/init"): "auth.init",
    ("POST", "/api/auth/change-password"): "auth.change_password",
    ("POST", "/api/auth/totp/setup"): "auth.totp_setup",
    ("POST", "/api/auth/totp/enable"): "auth.totp_enable",
    ("POST", "/api/auth/totp/disable"): "auth.totp_disable",
}

MAX_BODY_BYTES = 64 * 1024  # body 超过 64KB 不记摘要
MAX_DETAIL_LEN = 500


def log_operation(db, action: str, account_id: int | None = None, detail: str = "", operator: str = "web"):
    """后台 worker 手动记录审计日志（非 HTTP 请求，中间件覆盖不到）。"""
    db.add(OperationLog(action=action, account_id=account_id, detail=detail, operator=operator))
    db.commit()


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in SENSITIVE_SUBSTRS)


def _mask(obj):
    """递归脱敏：敏感 key 的值替换为 ***。"""
    if isinstance(obj, dict):
        return {k: ("***" if _is_sensitive(str(k)) else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj[:20]]
    return obj


def _summarize(path_params: dict, body) -> str:
    """拼关键参数摘要：路径参数 + body 前 8 个非敏感字段。"""
    parts = []
    for k, v in (path_params or {}).items():
        parts.append(f"{k}={v}")
    if isinstance(body, dict):
        for k, v in list(body.items())[:8]:
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            parts.append(f"{k}={s[:40]}")
    return " ".join(parts)[:MAX_DETAIL_LEN]


def _operator_from_request(request: Request) -> str:
    """从 Authorization: Bearer <JWT> 取操作人；无则记 anonymous。"""
    try:
        from app.core.security import decode_access_token

        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            username = decode_access_token(auth[7:].strip())
            if username:
                return username
    except Exception:
        pass
    return "anonymous"


class AuditMiddleware(BaseHTTPMiddleware):
    """审计中间件：写操作自动记 operation_logs。"""

    async def dispatch(self, request: Request, call_next):
        # 先读 body 并缓存（下游 request.json() 会复用缓存，不影响业务）
        cached_body = None
        if request.method in AUDIT_METHODS:
            try:
                ctype = request.headers.get("content-type", "")
                clen = int(request.headers.get("content-length") or 0)
                if "application/json" in ctype and 0 < clen <= MAX_BODY_BYTES:
                    raw = await request.body()
                    cached_body = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                cached_body = None

        response = await call_next(request)

        try:
            self._audit(request, response, cached_body)
        except Exception:
            logger.exception("审计日志写入失败（不影响业务）")
        return response

    def _audit(self, request: Request, response, cached_body) -> None:
        if request.method not in AUDIT_METHODS:
            return
        path = request.url.path
        if path.startswith(SKIP_PREFIXES):
            return
        route = request.scope.get("route")
        route_path = getattr(route, "path", path)
        if (request.method, route_path) in SKIP_EXACT:
            return

        action = ACTION_MAP.get(
            (request.method, route_path), f"{request.method.lower()}:{route_path}"
        )
        operator = _operator_from_request(request)

        # 路由可通过 request.state.audit_detail 覆写摘要（仍由中间件统一落库）；
        # auth 相关接口强制置空，密码/验证码绝不入库。
        state_detail = getattr(request.state, "audit_detail", None)
        if path.startswith(NO_DETAIL_PREFIXES):
            detail: str = ""
            account_id = None
        elif state_detail:
            detail = str(state_detail)[:MAX_DETAIL_LEN]
            account_id = getattr(request.state, "audit_account_id", None)
        else:
            path_params = dict(getattr(route, "path_params", {}) or {})
            # request.scope["path_params"] 是路由匹配后的实际参数，更可靠
            path_params = dict(request.scope.get("path_params") or path_params)
            detail = _summarize(path_params, _mask(cached_body) if cached_body is not None else None)
            account_id = None
            raw_aid = path_params.get("account_id")
            if raw_aid is None and isinstance(cached_body, dict):
                raw_aid = cached_body.get("account_id")
            try:
                account_id = int(raw_aid) if raw_aid is not None else None
            except (TypeError, ValueError):
                account_id = None

        if response.status_code >= 400:
            detail = (detail + f"（HTTP {response.status_code}）").strip()

        db = SessionLocal()
        try:
            db.add(
                OperationLog(
                    action=action, account_id=account_id, detail=detail, operator=operator
                )
            )
            db.commit()
        finally:
            db.close()
