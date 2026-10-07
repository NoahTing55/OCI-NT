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
        boot_volume_gb: int = 50,
    ) -> httpx.Response:
        """POST /20160918/instances/：创建实例（抢机核心调用）。

        Flex 机型必须带 shapeConfig；createVnicDetails.assignPublicIp=true
        让新实例自动分配临时公网 IP（后续可换预留 IP）。
        user_data 非空时通过 metadata 下发 base64 后的 cloud-init
        （用于开机自动设置 root 密码，见 core/cloud_init.py）。
        boot_volume_gb：启动卷大小（GB），OCI 限制 50-16384。
        """
        body: dict = {
            "compartmentId": compartment_id,
            "availabilityDomain": availability_domain,
            "shape": shape,
            "displayName": display_name,
            "sourceDetails": {
                "sourceType": "image",
                "imageId": image_ocid,
                "bootVolumeSizeInGBs": boot_volume_gb,
            },
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

    # ---------------- 账号信息（对标 OCI-Start OciClassLoader） ----------------
    # OCI-Start 真实逻辑（OciClassLoader.java:110-185）：
    # 1. 注册时间：GET /20160918/compartments/{tenancyId} 取 timeCreated（根 compartment）
    # 2. 账号类型：优先用区域订阅接口探测
    #    GET /20160918/regionSubscriptions?tenancyId={tenancyId}
    #    - 200 → upgraded（升级号，能订阅新区域）
    #    - 404/403 → free（免费号，无权限）
    #    - 其他异常 → 回退到 shapes 逻辑：有 AMD E3/E4/E5 大内存（memoryInGBs > 1.0）→ upgraded，否则 free
    #    只有 free / upgraded 两种，不再有 trial
    _PAID_AMD_SHAPES = frozenset({
        "vm.standard3.flex",
        "vm.standard.e4.flex",
        "vm.standard.e5.flex",
    })

    async def get_compartment(self, compartment_id: str) -> dict | None:
        """GET /20160918/compartments/{id}：取 compartment 详情（含 timeCreated）。失败返回 None。"""
        try:
            resp = await self.request("GET", "identity", f"/20160918/compartments/{compartment_id}")
            if resp.status_code == 200:
                return resp.json()
            logger.debug("查 compartment 失败：HTTP %s", resp.status_code)
        except Exception as e:
            logger.debug("查 compartment 异常：%s", str(e)[:100])
        return None

    async def list_shapes(self, compartment_id: str) -> list:
        """GET /20160918/shapes?compartmentId={id}：列出可用 shape。失败返回 []。"""
        try:
            resp = await self.request(
                "GET", "iaas",
                f"/20160918/shapes?compartmentId={compartment_id}&limit=100",
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("items", [])
            logger.debug("查 shapes 失败：HTTP %s", resp.status_code)
        except Exception as e:
            logger.debug("查 shapes 异常：%s", str(e)[:100])
        return []

    @staticmethod
    def _parse_ocid_time(ts: str | None) -> "datetime | None":
        """解析 OCI 返回的 ISO8601 时间（如 2024-03-15T10:30:00.000Z）。失败返回 None。"""
        if not ts:
            return None
        try:
            from datetime import datetime
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _is_older_than_one_month(dt: "datetime | None") -> bool:
        """判断时间是否超过 1 个月前。None 返回 False。"""
        if not dt:
            return False
        from datetime import datetime, timedelta
        return dt < datetime.utcnow() - timedelta(days=30)

    @classmethod
    def _can_create_large_amd(cls, shapes: list) -> bool:
        """检查 shapes 里是否有付费 AMD（E3/E4/E5）且 memoryInGBs > 1.0 且 billingType=Paid。

        按 OCI-Start AccountTypeEnum：BillingType.Paid 才是真正的付费（Pay As You Go）账号；
        Free Trial 账号也能看到 E3/E4/E5，但 billingType 是 LimitedFree，应判为免费。
        """
        for s in shapes:
            name = str(s.get("shape", "")).lower()
            if name not in cls._PAID_AMD_SHAPES:
                continue
            # billingType 必须是 Paid（Pay As You Go），LimitedFree（试用）不算
            billing = str(s.get("billingType", "")).upper()
            if billing != "PAID":
                continue
            try:
                if float(s.get("memoryInGBs") or 0) > 1.0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    async def get_tenancy_name(self) -> "str | None":
        """查租户显示名称：GET /20160918/tenancies/{tenancyId} 取 name 字段。失败返回 None，不抛异常。"""
        try:
            resp = await self.request("GET", "identity", f"/20160918/tenancies/{self.tenancy_ocid}")
            if resp.status_code == 200:
                return resp.json().get("name")
        except Exception:
            pass
        return None

    async def get_account_info(self) -> dict:
        """按 OCI-Start OciClassLoader 原逻辑识别账号信息。

        返回 {"registered_at": datetime|None, "account_type": "free"|"upgraded"|None}。
        注册时间：根 compartment 的 timeCreated。
        账号类型：ListShapes（compartmentId=tenancy OCID）查付费 AMD
          E3(VM.Standard3.Flex)/E4(VM.Standard.E4.Flex)/E5(VM.Standard.E5.Flex)
          且 memoryInGBs > 1.0：
            能开 → upgraded（OCI-Start 的 UPGRADE/TRIAL 两档合并为 upgraded）；
            不能开 → free。
        任何失败返回空值，不抛异常。
        """
        result: dict = {"registered_at": None, "account_type": None}
        try:
            # 注册时间：根 compartment 的 timeCreated
            comp = await self.get_compartment(self.tenancy_ocid)
            if comp:
                result["registered_at"] = self._parse_ocid_time(comp.get("timeCreated"))

            # 账号类型：ListShapes 查付费 AMD（OCI-Start 原逻辑）
            shapes = await self.list_shapes(self.tenancy_ocid)
            can_amd = self._can_create_large_amd(shapes)
            result["account_type"] = "upgraded" if can_amd else "free"

            logger.info("账号信息识别：type=%s, registered_at=%s",
                        result["account_type"], result["registered_at"])
        except Exception as e:
            logger.debug("识别账号信息异常：%s", str(e)[:100])
        return result

    # ---------------- 账号类型（订阅） ----------------
    async def get_tenancy_home_region(self) -> str | None:
        """查 tenancy 的 home region（GET /20160918/tenancies/{id} 返回 homeRegionKey）。"""
        try:
            resp = await self.request("GET", "identity", f"/20160918/tenancies/{self.tenancy_ocid}")
            if resp.status_code == 200:
                return resp.json().get("homeRegionKey")
        except Exception:
            pass
        return None

    async def get_subscription_info(self, home_region: str | None = None) -> dict:
        """查账号订阅信息：{type: free/paid/None, start_time: datetime/None}。

        调 osp-gateway Subscription API（identity 服务）：
        GET /20190111/subscriptions?compartmentId={tenancy}&ospHomeRegion={home}
        取第一条的 subscriptionTier（ALWAYS_FREE/FREE → free，PAID → paid）
        和 timeStart（订阅开始时间，即账号注册时间）。
        任何失败都返回 {type: None, start_time: None}，不抛异常。
        """
        result = {"type": None, "start_time": None}
        try:
            # home region 优先用 tenancy 的真实 home region（OSP Gateway 只在 home region 有 endpoint）
            hr = home_region or await self.get_tenancy_home_region() or self.region
            path = (
                "/20190111/subscriptions"
                f"?compartmentId={self.tenancy_ocid}&ospHomeRegion={hr}"
            )
            # 注意：Subscription API 属于 osp-gateway 服务（OCI-Start 用 SubscriptionServiceClient），
            # host 是 osp-gateway.{region}.oraclecloud.com，不是 identity
            logger.info("订阅查询：GET osp-gateway.%s %s", hr, path[:80])
            # endpoint host 用 home region
            url = f"https://osp-gateway.{hr}.oraclecloud.com{path}"
            headers = self._sign_headers("GET", url, None)
            resp = await self._client.request("GET", url, headers=headers)
            logger.info("订阅查询返回：HTTP %s", resp.status_code)
            if resp.status_code != 200:
                logger.warning("查询订阅列表失败：HTTP %s，body=%.200s", resp.status_code, resp.text)
                return result
            items = resp.json()
            # API 直接返回数组；兼容包一层的格式
            if isinstance(items, dict):
                items = items.get("items", [])
            if not items:
                return result
            sub = items[0]
            tier = str(sub.get("subscriptionTier", "")).upper()
            if tier in ("ALWAYS_FREE", "FREE"):
                result["type"] = "free"
            elif tier == "PAID":
                result["type"] = "paid"
            else:
                logger.debug("未知 subscriptionTier：%s", tier)
            # timeStart 是订阅开始时间（ISO8601），即账号注册时间
            ts = sub.get("timeStart") or sub.get("timeCreated")
            if ts:
                try:
                    from datetime import datetime
                    # 处理 "2024-01-15T10:30:00.000Z" 格式
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    result["start_time"] = dt.replace(tzinfo=None)
                except (ValueError, TypeError):
                    logger.debug("解析订阅时间失败：%s", ts)
            return result
        except Exception as e:
            logger.warning("查询订阅信息异常：%s", str(e)[:200], exc_info=True)
            return result

    async def get_subscription_type(self, home_region: str | None = None) -> str | None:
        """查账号订阅类型：free 免费 / paid 付费 / None 未知（兼容旧调用）。"""
        return (await self.get_subscription_info(home_region))["type"]

    # ---------------- 区域订阅（升级账户） ----------------
    async def list_region_subscriptions(self, tenancy_ocid: str) -> list[dict]:
        """GET /20160918/regionSubscriptions：查租户已订阅区域（含 regionName、status）。"""
        resp = await self.request(
            "GET", "identity", f"/20160918/regionSubscriptions?tenancyId={tenancy_ocid}"
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询区域订阅失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def create_region_subscription(self, tenancy_ocid: str, region_name: str) -> dict:
        """POST /20160918/regionSubscriptions：订阅新区域（仅升级账户可用）。"""
        resp = await self.request(
            "POST", "identity", "/20160918/regionSubscriptions",
            json_body={"tenancyId": tenancy_ocid, "regionName": region_name},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"订阅区域失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json() if resp.text else {}

    async def list_all_regions(self) -> list[dict]:
        """GET /20160918/regions：查全部可用区域（含 regionName、regionKey）。"""
        resp = await self.request("GET", "identity", "/20160918/regions")
        if resp.status_code != 200:
            raise RuntimeError(f"查询区域列表失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()
