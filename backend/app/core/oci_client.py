"""自研 OCI API 客户端：httpx 异步 + OCI Signature V1 手动签名。

为什么不用 oci-sdk-python：
1. 官方 SDK 是同步的（基于 requests），抢机高频并发不好做；
2. 自研可用 asyncio，单进程支撑多账号并发；
3. 每个账号持有独立 httpx.AsyncClient 并绑定独立代理，
   单API单代理天然实现，且代理故障只影响单个账号；
4. 可精细控制重试 / 退避 / 超时 / DNS 行为。

签名规范（Signature Version 1）：
- 待签头：(request-target)、host、date；
  POST/PUT 带 body 时追加 x-content-sha256、content-type、content-length
- Authorization: Signature version="1",keyId="tenancy_ocid/user_ocid/fingerprint",
  algorithm="rsa-sha256",headers="...",signature="..."
"""
import base64
import hashlib
import json as _json
import logging
from email.utils import formatdate
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


logger = logging.getLogger(__name__)


class OciClient:
    """一个实例 = 一个账号 = 一个代理。"""

    def __init__(
        self,
        *,
        tenancy_ocid: str,
        user_ocid: str,
        fingerprint: str,
        private_key_pem: str,
        region: str,
        proxy_url: str | None = None,
        timeout: float = 15.0,
    ):
        self.tenancy_ocid = tenancy_ocid
        self.user_ocid = user_ocid
        self.fingerprint = fingerprint
        self.region = region
        # keyId 格式：tenancy_ocid/user_ocid/fingerprint
        self.key_id = f"{tenancy_ocid}/{user_ocid}/{fingerprint}"
        self._private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
        # proxy_url 形如 http://user:pass@host:port 或 socks5h://host:port
        # socks5 必须用 socks5h（h = hostname），让 DNS 解析也走代理，
        # 防止 DNS 泄漏暴露服务器真实 IP
        # trust_env=False：忽略系统环境变量里的代理，保证「单API单代理」不被污染
        self._client = httpx.AsyncClient(proxy=proxy_url or None, timeout=timeout, trust_env=False)

    def _sign_headers(self, method: str, url: str, body: bytes | None) -> dict:
        parts = urlsplit(url)
        host = parts.netloc
        path = parts.path + (("?" + parts.query) if parts.query else "")
        date_str = formatdate(timeval=None, localtime=False, usegmt=True)

        headers = ["(request-target)", "host", "date"]
        lines = [
            f"(request-target): {method.lower()} {path}",
            f"host: {host}",
            f"date: {date_str}",
        ]
        if body is not None:
            sha256 = base64.b64encode(hashlib.sha256(body).digest()).decode()
            lines += [
                f"x-content-sha256: {sha256}",
                "content-type: application/json",
                f"content-length: {len(body)}",
            ]
            headers += ["x-content-sha256", "content-type", "content-length"]

        signing_string = "\n".join(lines)
        signature = self._private_key.sign(signing_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.b64encode(signature).decode()
        authorization = (
            f'Signature version="1",keyId="{self.key_id}",algorithm="rsa-sha256",'
            f'headers="{" ".join(headers)}",signature="{sig_b64}"'
        )

        req_headers = {"date": date_str, "host": host, "authorization": authorization}
        if body is not None:
            req_headers.update(
                {
                    "x-content-sha256": sha256,
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                }
            )
        return req_headers

    async def request(self, method: str, service: str, path: str, json_body: dict | None = None) -> httpx.Response:
        """发往 https://{service}.{region}.oraclecloud.com{path} 的签名请求。"""
        url = f"https://{service}.{self.region}.oraclecloud.com{path}"
        body = _json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = self._sign_headers(method, url, body)
        return await self._client.request(method, url, headers=headers, content=body)

    # ---------------- 存活检查 ----------------
    async def get_user(self) -> httpx.Response:
        """GET /20160918/users/{userId}：账号存活检查。"""
        return await self.request("GET", "identity", f"/20160918/users/{self.user_ocid}")

    # ---------------- 实例管理（M2） ----------------
    async def list_instances(self, compartment_id: str) -> list[dict]:
        """GET /20160918/instances：分页拉取 compartment 下所有实例（列表接口返回裸 JSON 数组）。"""
        items: list[dict] = []
        page = None
        while True:
            path = f"/20160918/instances?compartmentId={compartment_id}&limit=100"
            if page:
                path += f"&page={page}"
            resp = await self.request("GET", "iaas", path)
            if resp.status_code != 200:
                raise RuntimeError(f"查询实例失败：HTTP {resp.status_code} {resp.text[:200]}")
            items.extend(resp.json())
            page = resp.headers.get("opc-next-page")
            if not page:
                break
        return items

    async def instance_action(self, instance_id: str, action: str) -> httpx.Response:
        """POST /20160918/instances/{id}/actions：START / STOP / RESET / SOFTSTOP / SOFTRESET。"""
        return await self.request(
            "POST", "iaas", f"/20160918/instances/{instance_id}/actions", {"action": action}
        )

    async def terminate_instance(self, instance_id: str) -> httpx.Response:
        """DELETE /20160918/instances/{id}：终止实例（不可逆）。"""
        return await self.request("DELETE", "iaas", f"/20160918/instances/{instance_id}")

    async def update_instance(self, instance_id: str, details: dict) -> httpx.Response:
        """PUT /20160918/instances/{id}：更新 displayName / freeformTags / definedTags / metadata。

        注意：shape / OCPU / 内存不支持在线变更，调用方需提前拦截。
        """
        return await self.request("PUT", "iaas", f"/20160918/instances/{instance_id}", details)

    # ---------------- 抢机（M3） ----------------
    async def launch_instance(
        self,
        *,
        compartment_id: str,
        availability_domain: str,
        shape: str,
        ocpus: float,
        memory_gb: float,
        image_ocid: str,
        subnet_ocid: str,
        display_name: str,
        user_data: str = "",
    ) -> httpx.Response:
        """POST /20160918/instances/：创建实例（抢机核心调用）。

        Flex 机型必须带 shapeConfig；createVnicDetails.assignPublicIp=true
        让新实例自动分配临时公网 IP（后续可换预留 IP）。
        user_data 非空时通过 metadata 下发 base64 后的 cloud-init
        （用于开机自动设置 root 密码，见 core/cloud_init.py）。
        """
        body: dict = {
            "compartmentId": compartment_id,
            "availabilityDomain": availability_domain,
            "shape": shape,
            "displayName": display_name,
            "sourceDetails": {"sourceType": "image", "imageId": image_ocid},
            "createVnicDetails": {"subnetId": subnet_ocid, "assignPublicIp": True},
        }
        if user_data:
            body["metadata"] = {"user_data": user_data}
        # Flex 机型需要 shapeConfig；固定机型传了会被 400，直接不传
        if shape.endswith(".Flex"):
            body["shapeConfig"] = {"ocpus": ocpus, "memoryInGBs": memory_gb}
        return await self.request("POST", "iaas", "/20160918/instances/", body)

    # ---------------- 网络 / IP（M2） ----------------
    async def list_vnic_attachments(self, compartment_id: str, instance_id: str) -> list[dict]:
        """GET /20160918/vnicAttachments：查实例的 VNIC 附件（取第一个为主 VNIC）。"""
        resp = await self.request(
            "GET", "iaas",
            f"/20160918/vnicAttachments?compartmentId={compartment_id}&instanceId={instance_id}&limit=10",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询 VNIC 附件失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_vnic(self, vnic_id: str) -> httpx.Response:
        """GET /20160918/vnics/{vnicId}：返回 publicIp / privateIp 等。"""
        return await self.request("GET", "iaas", f"/20160918/vnics/{vnic_id}")

    async def list_private_ips(self, vnic_id: str) -> list[dict]:
        """GET /20160918/privateIps?vnicId=：找 isPrimary 的主私网 IP。"""
        resp = await self.request("GET", "iaas", f"/20160918/privateIps?vnicId={vnic_id}&limit=50")
        if resp.status_code != 200:
            raise RuntimeError(f"查询私网 IP 失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_private_ip(self, private_ip_id: str) -> httpx.Response:
        """GET /20160918/privateIps/{id}：返回 publicIpId（判断旧公网 IP 用）。"""
        return await self.request("GET", "iaas", f"/20160918/privateIps/{private_ip_id}")

    async def create_public_ip(self, compartment_id: str, display_name: str = "") -> httpx.Response:
        """POST /20160918/publicIps：创建预留 IP（lifetime=RESERVED）。"""
        body = {"compartmentId": compartment_id, "lifetime": "RESERVED"}
        if display_name:
            body["displayName"] = display_name
        return await self.request("POST", "iaas", "/20160918/publicIps", body)

    async def update_public_ip(self, public_ip_id: str, private_ip_id: str) -> httpx.Response:
        """PUT /20160918/publicIps/{id}：把预留 IP 绑定到私网 IP。

        同一私网 IP 同时只能绑一个公网 IP，新绑定会自动顶掉旧的。
        """
        return await self.request(
            "PUT", "iaas", f"/20160918/publicIps/{public_ip_id}", {"privateIpId": private_ip_id}
        )

    async def delete_public_ip(self, public_ip_id: str) -> httpx.Response:
        """DELETE /20160918/publicIps/{id}：释放预留 IP（仅限 RESERVED）。"""
        return await self.request("DELETE", "iaas", f"/20160918/publicIps/{public_ip_id}")

    async def list_public_ips(self, compartment_id: str, private_ip_id: str | None = None) -> list[dict]:
        """GET /20160918/publicIps：可按 privateIpId 过滤，查旧公网 IP 的 id / lifetime 用。"""
        path = f"/20160918/publicIps?compartmentId={compartment_id}&limit=100"
        if private_ip_id:
            path += f"&privateIpId={private_ip_id}"
        resp = await self.request("GET", "iaas", path)
        if resp.status_code != 200:
            raise RuntimeError(f"查询公网 IP 失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def aclose(self):
        await self._client.aclose()

    # ---------------- 配额查询（Limits API） ----------------
    # OCI-Start OciLimitsUtils.getResourceAvailability 思路：
    # GET https://limits.{region}.oraclecloud.com/20181004/services/compute/limits/{limitName}
    #     ?compartmentId={tenancy_ocid}
    # 返回 ResourceAvailability：{"available": 余量, "used": 已用}
    async def get_compute_quota(self, compartment_id: str, limit_name: str) -> dict:
        """查 compute 服务某项配额。返回 {"available": int, "used": int}；失败返回 None 值，不抛异常。"""
        try:
            resp = await self.request(
                "GET", "limits",
                f"/20181004/services/compute/limits/{limit_name}?compartmentId={compartment_id}",
            )
            if resp.status_code != 200:
                logger.warning("配额查询 %s 失败：HTTP %s", limit_name, resp.status_code)
                return {"available": None, "used": None}
            data = resp.json()
            return {"available": data.get("available"), "used": data.get("used")}
        except Exception as e:
            logger.warning("配额查询 %s 异常：%s", limit_name, str(e)[:120])
            return {"available": None, "used": None}

    # ---------------- 一键建网（VCN / IG / 路由表 / 子网） ----------------
    # OCI-Start buildSimpleAllNetWork 思路：表单不让用户手填子网，后端按
    # 「有则复用、无则创建」自动备好网络。以下均为原子 API 封装，编排逻辑
    # 在 api/oci_options.py 的 ensure_network 里。
    async def list_vcns(self, compartment_id: str) -> list[dict]:
        """GET /20160918/vcns：查 compartment 下的 VCN 列表。"""
        resp = await self.request(
            "GET", "iaas", f"/20160918/vcns?compartmentId={compartment_id}&limit=50"
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询 VCN 失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_vcn(self, vcn_id: str) -> httpx.Response:
        """GET /20160918/vcns/{vcnId}：查 VCN 详情 / 状态（轮询等 AVAILABLE 用）。"""
        return await self.request("GET", "iaas", f"/20160918/vcns/{vcn_id}")

    async def create_vcn(
        self,
        compartment_id: str,
        cidr_block: str = "10.0.0.0/16",
        display_name: str = "oci-panel-vcn",
    ) -> httpx.Response:
        """POST /20160918/vcns：创建 VCN（异步，建完需轮询到 AVAILABLE）。"""
        return await self.request(
            "POST", "iaas", "/20160918/vcns",
            {"compartmentId": compartment_id, "cidrBlock": cidr_block, "displayName": display_name},
        )

    async def list_internet_gateways(self, compartment_id: str, vcn_id: str) -> list[dict]:
        """GET /20160918/internetGateways：查 VCN 下的 Internet Gateway 列表。"""
        resp = await self.request(
            "GET", "iaas",
            f"/20160918/internetGateways?compartmentId={compartment_id}&vcnId={vcn_id}&limit=50",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询 Internet Gateway 失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def create_internet_gateway(
        self,
        compartment_id: str,
        vcn_id: str,
        display_name: str = "oci-panel-ig",
    ) -> httpx.Response:
        """POST /20160918/internetGateways：创建 Internet Gateway（默认启用）。"""
        return await self.request(
            "POST", "iaas", "/20160918/internetGateways",
            {
                "compartmentId": compartment_id, "vcnId": vcn_id,
                "displayName": display_name, "isEnabled": True,
            },
        )

    async def list_route_tables(self, compartment_id: str, vcn_id: str) -> list[dict]:
        """GET /20160918/routeTables：查 VCN 下的路由表列表。"""
        resp = await self.request(
            "GET", "iaas",
            f"/20160918/routeTables?compartmentId={compartment_id}&vcnId={vcn_id}&limit=50",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询路由表失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def update_route_table(self, rt_id: str, route_rules: list[dict]) -> httpx.Response:
        """PUT /20160918/routeTables/{rtId}：整体替换路由规则（调用方需先读出现有规则再追加）。"""
        return await self.request(
            "PUT", "iaas", f"/20160918/routeTables/{rt_id}", {"routeRules": route_rules}
        )

    async def list_subnets_of_vcn(self, compartment_id: str, vcn_id: str) -> list[dict]:
        """GET /20160918/subnets：查指定 VCN 下的所有子网。"""
        resp = await self.request(
            "GET", "iaas",
            f"/20160918/subnets?compartmentId={compartment_id}&vcnId={vcn_id}&limit=100",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询子网失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def create_subnet(
        self,
        compartment_id: str,
        vcn_id: str,
        availability_domain: str,
        cidr_block: str = "10.0.0.0/24",
        display_name: str = "oci-panel-subnet",
        route_table_id: str = "",
    ) -> httpx.Response:
        """POST /20160918/subnets：创建子网。

        prohibitPublicIpOnVnic=false 做公网子网（抢机要分配公网 IP）；
        route_table_id 为空则用 VCN 默认路由表。
        """
        body: dict = {
            "compartmentId": compartment_id, "vcnId": vcn_id,
            "availabilityDomain": availability_domain, "cidrBlock": cidr_block,
            "displayName": display_name, "prohibitPublicIpOnVnic": False,
        }
        if route_table_id:
            body["routeTableId"] = route_table_id
        return await self.request("POST", "iaas", "/20160918/subnets", body)

    async def get_subnet(self, subnet_id: str) -> dict:
        """GET /20160918/subnets/{subnetId}：查子网详情（含 securityListIds）。"""
        resp = await self.request("GET", "iaas", f"/20160918/subnets/{subnet_id}")
        if resp.status_code != 200:
            raise RuntimeError(f"查询子网详情失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_security_list(self, security_list_id: str) -> dict:
        """GET /20160918/securityLists/{id}：查安全列表（含 ingressSecurityRules）。"""
        resp = await self.request("GET", "iaas", f"/20160918/securityLists/{security_list_id}")
        if resp.status_code != 200:
            raise RuntimeError(f"查询安全列表失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def update_security_list(self, security_list_id: str, ingress_rules: list[dict]) -> httpx.Response:
        """PUT /20160918/securityLists/{id}：整体替换入站规则（调用方需先读出现有规则再追加）。"""
        return await self.request(
            "PUT", "iaas", f"/20160918/securityLists/{security_list_id}",
            {"ingressSecurityRules": ingress_rules},
        )
