"""Cloudflare DNS 同步：Token 加密存储，域名-IP 自动绑定。

Token 权限最小化：只需要 Zone.DNS 的 Edit 权限，建议在 CF 后台按 zone
限制 Token 作用域；Token 明文只在内存中出现，入库前 Fernet 加密，永不返回给前端。

触发点：
- 换 IP 成功后：sync_instance_domains() 自动更新该实例所有 auto_sync 域名；
- 抢机成功后（M3）：新实例 IP → 绑定域名自动建记录（调用 ensure_record 即可）；
- 手动：API 的 /sync、/sync-all、一键巡检 /check。
"""
import logging
from datetime import datetime

import httpx

from app.core.security import decrypt_text
from app.models.models import CloudflareToken, DomainBinding

logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    """CF API 返回 success=false 或网络异常时抛出。"""


class CloudflareClient:
    """CF API 客户端（Bearer Token 认证）。用完记得 await aclose()。"""

    def __init__(self, token: str, timeout: float = 15.0):
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout,
            # 直连 CF API：忽略环境变量里的代理，避免被污染或解析失败
            trust_env=False,
        )

    async def _api(self, method: str, path: str, json_body: dict | None = None) -> object:
        resp = await self._client.request(method, path, json=json_body)
        data = resp.json()
        if not data.get("success"):
            errors = data.get("errors", []) or []
            msg = "; ".join(e.get("message", str(e)) for e in errors) or f"HTTP {resp.status_code}"
            raise CloudflareError(f"CF API 失败：{msg}")
        return data.get("result")

    async def list_zones(self, name: str | None = None) -> list:
        path = "/zones?per_page=50" + (f"&name={name}" if name else "")
        return await self._api("GET", path)

    async def find_zone_id(self, domain: str) -> str | None:
        """按域名找 zone：逐级剥子域名后缀匹配。

        如 a.b.example.com 依次尝试 a.b.example.com / b.example.com / example.com。
        """
        zones = await self.list_zones()
        zone_names = {z["name"].lower(): z["id"] for z in zones}
        labels = domain.lower().strip().split(".")
        for i in range(len(labels) - 1):
            candidate = ".".join(labels[i:])
            if candidate in zone_names:
                return zone_names[candidate]
        return None

    async def list_records(
        self, zone_id: str, name: str | None = None, record_type: str | None = None
    ) -> list:
        path = f"/zones/{zone_id}/dns_records?per_page=100"
        if name:
            path += f"&name={name}"
        if record_type:
            path += f"&type={record_type}"
        return await self._api("GET", path)

    async def create_record(
        self, zone_id: str, record_type: str, name: str, content: str,
        ttl: int = 120, proxied: bool = False,
    ) -> dict:
        return await self._api("POST", f"/zones/{zone_id}/dns_records", {
            "type": record_type, "name": name, "content": content,
            "ttl": ttl, "proxied": proxied,
        })

    async def update_record(
        self, zone_id: str, record_id: str, record_type: str, name: str, content: str,
        ttl: int = 120, proxied: bool = False,
    ) -> dict:
        return await self._api("PUT", f"/zones/{zone_id}/dns_records/{record_id}", {
            "type": record_type, "name": name, "content": content,
            "ttl": ttl, "proxied": proxied,
        })

    async def ensure_record(
        self, domain: str, ip: str, record_type: str = "A", ttl: int = 120, proxied: bool = False
    ) -> dict:
        """有则更新、无则创建。返回 {"record_id", "created", "unchanged"}。"""
        zone_id = await self.find_zone_id(domain)
        if not zone_id:
            raise CloudflareError(f"CF 上找不到域名 {domain} 的 zone（Token 权限或域名有误）")
        records = await self.list_records(zone_id, name=domain, record_type=record_type)
        if records:
            rec = records[0]
            if rec.get("content") == ip:
                return {"record_id": rec["id"], "created": False, "unchanged": True}
            updated = await self.update_record(zone_id, rec["id"], record_type, domain, ip, ttl, proxied)
            return {"record_id": updated["id"], "created": False, "unchanged": False}
        created = await self.create_record(zone_id, record_type, domain, ip, ttl, proxied)
        return {"record_id": created["id"], "created": True, "unchanged": False}

    async def aclose(self):
        await self._client.aclose()


async def sync_instance_domains(db, instance_ocid: str, new_ip: str) -> list:
    """换 IP 成功后自动调用：更新该实例所有 auto_sync 域名绑定的 CF 记录。

    返回 [{"domain", "ok", "record_id"/"error"}]，供换 IP 接口汇总展示。
    单个域名失败不影响其他域名。
    """
    results = []
    bindings = (
        db.query(DomainBinding)
        .filter(DomainBinding.instance_ocid == instance_ocid, DomainBinding.auto_sync == True)
        .all()
    )
    for b in bindings:
        token_row = db.get(CloudflareToken, b.cf_token_id)
        if not token_row:
            results.append({"domain": b.domain, "ok": False, "error": "CF Token 不存在"})
            continue
        try:
            client = CloudflareClient(decrypt_text(token_row.token_enc))
            try:
                info = await client.ensure_record(b.domain, new_ip, b.record_type, ttl=b.ttl, proxied=b.proxied)
            finally:
                await client.aclose()
            b.record_id = info["record_id"]
            b.last_ip = new_ip
            b.last_sync_at = datetime.utcnow()
            db.commit()
            results.append({"domain": b.domain, "ok": True, "record_id": info["record_id"]})
            logger.info("CF 同步成功：%s → %s", b.domain, new_ip)
        except Exception as e:
            logger.warning("CF 同步失败：%s %s", b.domain, e)
            results.append({"domain": b.domain, "ok": False, "error": str(e)[:200]})
    return results
