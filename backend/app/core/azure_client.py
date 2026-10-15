"""Azure 服务主体认证 + Management API 封装（只读）。

- 认证：OAuth2 client_credentials，token 内存缓存（提前 5 分钟刷新）；
- 订阅：GET /subscriptions，取 displayName / subscriptionId / state；
- Tier：GET /subscriptions/{id}/providers/Microsoft.CognitiveServices/quotaTiers
  （api-version=2025-10-01-preview），取 value[0].properties.currentTierName；
- 消费：Cost Management query，取本月累计（可选，失败不阻塞主流程）。

所有密钥经 app.core.security Fernet 加解密，日志中永不打印密钥明文。
"""
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_MGMT = "https://management.azure.com"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_SCOPE = "https://management.azure.com/.default"

# (tenant_id, client_id) -> {"token": str, "expires_at": float}
_token_cache: dict[tuple[str, str], dict] = {}


def _cache_key(tenant_id: str, client_id: str) -> tuple[str, str]:
    return (tenant_id.strip(), client_id.strip())


def get_azure_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """取 Azure Management API token（带缓存）。失败抛 RuntimeError。"""
    key = _cache_key(tenant_id, client_id)
    cached = _token_cache.get(key)
    if cached and cached["expires_at"] > time.time() + 300:
        return cached["token"]

    url = _TOKEN_URL.format(tenant=tenant_id.strip())
    try:
        resp = httpx.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id.strip(),
                "client_secret": client_secret,
                "scope": _SCOPE,
            },
            timeout=15,
        )
    except Exception as e:
        raise RuntimeError(f"Azure 认证请求失败：{e}") from e

    if resp.status_code != 200:
        # 只取错误摘要，不打密钥
        try:
            err = resp.json().get("error_description", resp.text)[:200]
        except Exception:
            err = resp.text[:200]
        raise RuntimeError(f"Azure 认证失败（HTTP {resp.status_code}）：{err}")

    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError("Azure 认证失败：响应中无 access_token")
    expires_in = int(data.get("expires_in", 3600))
    _token_cache[key] = {"token": token, "expires_at": time.time() + expires_in}
    return token


def invalidate_token(tenant_id: str, client_id: str) -> None:
    """清掉缓存的 token（如下次调用 401 时）。"""
    _token_cache.pop(_cache_key(tenant_id, client_id), None)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def list_subscriptions(token: str) -> list[dict]:
    """列出该服务主体可见的订阅。失败抛 RuntimeError。"""
    url = f"{_MGMT}/subscriptions?api-version=2022-12-01"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=20)
    except Exception as e:
        raise RuntimeError(f"查询订阅失败：{e}") from e
    if resp.status_code == 401:
        raise RuntimeError("AUTH_EXPIRED")
    if resp.status_code != 200:
        raise RuntimeError(f"查询订阅失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    out = []
    for s in resp.json().get("value", []):
        out.append({
            "subscription_id": s.get("subscriptionId", ""),
            "display_name": s.get("displayName", ""),
            "state": s.get("state", ""),
        })
    return out


def get_quota_tier(token: str, subscription_id: str) -> dict:
    """查单个订阅的 CognitiveServices quotaTier（用户截图里的 Tier 等级）。

    返回 {"tier": "Free Tier"/..., "raw": ...}；查不到返回 {"tier": ""}，不抛异常
    （配额 API 在部分订阅/区域不可用是正常情况）。
    """
    url = (
        f"{_MGMT}/subscriptions/{subscription_id}"
        f"/providers/Microsoft.CognitiveServices/quotaTiers"
        f"?api-version=2025-10-01-preview"
    )
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=20)
    except Exception as e:
        logger.warning("Tier 查询网络异常 sub=%s：%s", subscription_id[:8], e)
        return {"tier": ""}
    if resp.status_code != 200:
        logger.warning("Tier 查询 HTTP %s sub=%s", resp.status_code, subscription_id[:8])
        return {"tier": ""}
    try:
        values = resp.json().get("value", [])
        tier = (values[0].get("properties", {}) or {}).get("currentTierName", "") if values else ""
        return {"tier": tier or ""}
    except Exception as e:
        logger.warning("Tier 解析异常 sub=%s：%s", subscription_id[:8], e)
        return {"tier": ""}


def get_month_cost(token: str, subscription_id: str) -> dict:
    """查订阅本月累计消费（USD）。失败返回 {"cost": None}，不阻塞主流程。"""
    url = (
        f"{_MGMT}/subscriptions/{subscription_id}"
        f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    )
    body = {
        "type": "ActualCost",
        "timeframe": "MonthToDate",
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
        },
    }
    try:
        resp = httpx.post(url, headers={**_headers(token), "Content-Type": "application/json"},
                          json=body, timeout=25)
    except Exception as e:
        logger.warning("消费查询网络异常 sub=%s：%s", subscription_id[:8], e)
        return {"cost": None, "currency": ""}
    if resp.status_code != 200:
        logger.warning("消费查询 HTTP %s sub=%s", resp.status_code, subscription_id[:8])
        return {"cost": None, "currency": ""}
    try:
        rows = resp.json().get("properties", {}).get("rows", [])
        cols = resp.json().get("properties", {}).get("columns", [])
        if not rows:
            return {"cost": 0.0, "currency": ""}
        # 找到 Cost 列
        cost_idx = next((i for i, c in enumerate(cols) if c.get("name") == "Cost"), 0)
        curr_idx = next((i for i, c in enumerate(cols) if c.get("name") == "Currency"), None)
        return {
            "cost": float(rows[0][cost_idx]),
            "currency": rows[0][curr_idx] if curr_idx is not None else "",
        }
    except Exception as e:
        logger.warning("消费解析异常 sub=%s：%s", subscription_id[:8], e)
        return {"cost": None, "currency": ""}
