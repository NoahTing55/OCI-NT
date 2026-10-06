"""代理测速：经代理请求轻量探针 URL，测延迟并判定可用性。

- 失效只走 TG 告警，不自动换绑（避免 IP 跳变触发 Oracle 风控）；
- 探针用 api.ipify.org：响应体极小（纯文本出口 IP），适合测速，
  顺带能看到代理出口 IP 是否符合预期。
"""
import logging
import time

import httpx

from app.core.security import decrypt_text

logger = logging.getLogger(__name__)

PROBE_URL = "https://api.ipify.org"


async def check_proxy(proxy) -> tuple:
    """返回 (是否可用, 延迟毫秒或 None)。"""
    auth = ""
    if proxy.username:
        pwd = decrypt_text(proxy.password_enc) if proxy.password_enc else ""
        auth = f"{proxy.username}:{pwd}@"
    scheme = "socks5h" if proxy.scheme == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{proxy.host}:{proxy.port}"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=10, trust_env=False) as client:
            r = await client.get(PROBE_URL)
            ok = r.status_code == 200
    except Exception as e:
        logger.warning("代理「%s」测速失败：%s", proxy.name, e)
        return False, None
    latency = int((time.monotonic() - start) * 1000)
    return ok, latency
