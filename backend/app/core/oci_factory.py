"""OciClient 工厂：从 DB 的 Account 记录构建绑定好代理的客户端。

把「解密私钥 + 拼代理 URL」的逻辑收拢到一处，各业务模块（存活检查、
实例查询、批量任务、换 IP）都走这里，保证单API单代理一致。
"""
from app.core.oci_client import OciClient
from app.core.security import decrypt_text
from app.models.models import Account


def build_proxy_url(account: Account) -> str | None:
    """按账号绑定的代理拼出 httpx proxy URL（socks5 用 socks5h 防 DNS 泄漏）。"""
    p = account.proxy
    if not p:
        return None
    auth = ""
    if p.username:
        pwd = decrypt_text(p.password_enc) if p.password_enc else ""
        auth = f"{p.username}:{pwd}@"
    scheme = "socks5h" if p.scheme == "socks5" else "http"
    return f"{scheme}://{auth}{p.host}:{p.port}"


def build_client_for_account(account: Account, timeout: float = 20.0) -> OciClient:
    """解密私钥并返回绑定好该账号代理的 OciClient。

    注意：调用方负责 `await client.aclose()` 释放连接。
    """
    private_key_pem = decrypt_text(account.private_key_enc)
    return OciClient(
        tenancy_ocid=account.tenancy_ocid,
        user_ocid=account.user_ocid,
        fingerprint=account.fingerprint,
        private_key_pem=private_key_pem,
        region=account.region,
        proxy_url=build_proxy_url(account),
        timeout=timeout,
    )


def compartment_of(account: Account) -> str:
    """实例/网络类查询用的 compartment：优先用账号配置的，空则用 tenancy OCID（根 compartment）。"""
    return (account.compartment_ocid or "").strip() or account.tenancy_ocid
