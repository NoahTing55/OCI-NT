"""Pydantic 请求 / 响应模型。

注意：私钥明文只在创建 / 更新账号的请求体里出现一次（服务端立即加密入库），
任何返回模型都不包含私钥、代理密码等敏感字段。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------- 账号 ----------------
class AccountCreate(BaseModel):
    name: str = Field(..., max_length=100)
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    private_key: str = Field(..., description="PEM 私钥明文，服务端加密存储")
    region: str = "ap-seoul-1"
    # 实例列表查询用的 compartment OCID；为空则用 tenancy OCID（根 compartment）
    compartment_ocid: str = ""
    remark: str = ""
    # 新建时直接绑定的代理 id；None 表示直连
    proxy_id: int | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    region: str | None = None
    compartment_ocid: str | None = None
    remark: str | None = None
    cost: float | None = None
    # 真实注册时间（ISO 字符串或 datetime；传 null 清空则用 created_at 兜底）
    registered_at: datetime | None = None
    account_type: str | None = None
    private_key: str | None = Field(default=None, description="传了才更新私钥")


class ProxyBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    scheme: str
    host: str
    port: int


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    region: str
    compartment_ocid: str = ""
    proxy_id: int | None
    status: str
    last_check_at: datetime | None
    remark: str
    proxy: ProxyBrief | None = None
    created_at: datetime | None = None  # 账号创建时间（存活天数用）
    instance_count: int = 0  # 该账号实例数（从实例缓存统计，无缓存则为 0）
    # 该账号抢机任务状态：running（有进行中）/ paused（有已暂停）/ none（无）
    snipe_task_status: str = "none"
    cost: float = 0  # 账号成本（OCI-Start 式，可点击修改）
    registered_at: datetime | None = None  # 真实注册时间（存活天数优先用它）
    account_type: str = ""  # free 免费 / paid 付费 / "" 未知


class BindProxyIn(BaseModel):
    proxy_id: int | None = Field(description="要绑定的代理 id；null 表示解绑")


# ---------------- 代理 ----------------
class ProxyCreate(BaseModel):
    name: str = Field(..., max_length=100)
    scheme: str = Field(default="http", description="http 或 socks5")
    host: str
    port: int
    username: str = ""
    password: str = Field(default="", description="明文密码，服务端加密存储")
    remark: str = ""


class ProxyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    scheme: str
    host: str
    port: int
    username: str
    status: str
    latency_ms: int | None
    remark: str
    # 该代理当前绑定的账号别名；未绑定为 None
    bound_account_name: str | None = None


# ---------------- 存活检查 ----------------
class CheckResult(BaseModel):
    account_id: int
    name: str
    status: str
    status_code: int | None
    message: str


# ---------------- 实例 ----------------
class InstanceItem(BaseModel):
    account_id: int
    account_name: str
    region: str
    instance_id: str
    display_name: str | None = None
    lifecycle_state: str | None = None
    shape: str | None = None
    availability_domain: str | None = None
    compartment_id: str | None = None
    time_created: str | None = None
    public_ip: str | None = None
    private_ip: str | None = None
    ip_error: str | None = None


class InstanceListOut(BaseModel):
    items: list[InstanceItem]
    errors: list[dict] = []


class InstanceEditIn(BaseModel):
    """编辑实例：只允许 display_name / freeform_tags / metadata。

    extra="allow"：收到 shape 等字段时手动拦截，给出明确中文错误，
    而不是让 pydantic 报晦涩的校验错误。
    """

    model_config = ConfigDict(extra="allow")

    display_name: str | None = None
    freeform_tags: dict | None = None
    metadata: dict | None = None


# ---------------- 批量任务 ----------------
class BatchItemIn(BaseModel):
    account_id: int
    instance_id: str
    display_name: str = ""


class BatchCreateIn(BaseModel):
    task_type: str = Field(description="power_on / power_off / reboot / terminate")
    items: list[BatchItemIn]
    name: str = ""
    confirm: bool = Field(default=False, description="terminate 必须 confirm=true 二次确认")


class BatchTaskOut(BaseModel):
    id: int
    name: str
    task_type: str
    status: str
    total: int
    success_count: int
    fail_count: int
    detail: dict = {}
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------- 换 IP ----------------
class ChangeIpIn(BaseModel):
    account_id: int
    instance_id: str
    release_old: bool = Field(default=True, description="更换成功后释放旧的预留 IP（仅 RESERVED 生效）")


class OpenPortsIn(BaseModel):
    account_id: int
    instance_id: str = Field(description="实例 OCID")


class CfSyncResult(BaseModel):
    domain: str
    ok: bool
    record_id: str | None = None
    error: str | None = None


class ChangeIpOut(BaseModel):
    old_ip: str | None = None
    new_ip: str | None = None
    new_public_ip_id: str | None = None
    released_old_public_ip_id: str | None = None
    cf_synced: list[CfSyncResult] = []


# ---------------- Cloudflare ----------------
class CfTokenCreate(BaseModel):
    name: str = Field(..., max_length=100)
    token: str = Field(..., description="CF API Token 明文，服务端加密存储")
    remark: str = ""


class CfTokenOut(BaseModel):
    id: int
    name: str
    remark: str
    created_at: datetime | None = None


class DomainBindingCreate(BaseModel):
    cf_token_id: int
    account_id: int | None = None
    instance_ocid: str = ""
    instance_name: str = ""
    domain: str
    record_type: str = Field(default="A", description="A 或 AAAA")
    proxied: bool = Field(default=False, description="CF 小黄云")
    ttl: int = Field(default=120, description="DNS TTL（秒）")
    auto_sync: bool = Field(default=True, description="换 IP 后自动同步")


class DomainBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cf_token_id: int | None
    account_id: int | None
    instance_ocid: str
    instance_name: str
    domain: str
    record_type: str
    zone_id: str
    record_id: str
    proxied: bool
    ttl: int
    auto_sync: bool
    last_ip: str
    last_sync_at: datetime | None = None


# ---------------- 抢机任务 ----------------
class EnsureNetworkIn(BaseModel):
    """一键建网请求：按「有则复用、无则创建」备好 VCN→IG→路由→子网。"""
    account_id: int
    region: str = ""
    availability_domain: str = Field(default="", description="为空则取该区域第一个可用域")
    compartment_id: str = Field(default="", description="为空则用 tenancy OCID（根 compartment）")


class SnipeTaskCreate(BaseModel):
    account_id: int
    region: str
    shape: str = Field(default="VM.Standard.A1.Flex")
    ocpus: float = Field(default=4, gt=0)
    memory_gb: float = Field(default=24, gt=0)
    image_ocid: str = Field(..., description="sourceDetails.imageId")
    subnet_ocid: str = Field(default="", description="createVnicDetails.subnetId；为空则任务启动时自动建网")
    availability_domain: str
    display_name: str = Field(default="", max_length=128)
    root_password: str = Field(default="", max_length=128, description="root 密码；为空则 worker 自动生成随机密码")
    target_count: int = Field(default=1, ge=1, le=100, description="目标抢机台数，一次抢 N 台")
    interval_seconds: int = Field(default=60, ge=5, le=3600, description="无可用容量时的重试间隔（秒）")
    open_all_ports: bool = Field(default=True, description="开机后是否放行所有端口（安全列表加全端口规则）")
    boot_volume_gb: int = Field(default=50, ge=50, le=16384, description="启动卷大小（GB），OCI 限制 50-16384")


class SnipeTaskUpdate(BaseModel):
    """抢机任务编辑：所有字段可选，只更新传入的非 None 字段。"""
    account_id: int | None = None
    region: str | None = None
    shape: str | None = None
    ocpus: float | None = Field(default=None, gt=0)
    memory_gb: float | None = Field(default=None, gt=0)
    image_ocid: str | None = None
    subnet_ocid: str | None = None
    availability_domain: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    root_password: str | None = Field(default=None, max_length=128)
    target_count: int | None = Field(default=None, ge=1, le=100)
    interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    open_all_ports: bool | None = None
    boot_volume_gb: int | None = Field(default=None, ge=50, le=16384)


class SnipeTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    account_name: str = ""
    region: str
    shape: str
    ocpus: float | None = None
    memory_gb: float | None = None
    image_ocid: str = ""
    subnet_ocid: str = ""
    availability_domain: str = ""
    display_name: str = ""
    status: str
    attempts: int = 0
    last_error: str = ""
    instance_ocid: str = ""
    target_count: int = 1
    success_count: int = 0
    interval_seconds: int = 60
    open_all_ports: bool = True
    boot_volume_gb: int = 50
    root_password: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class SnipeLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    level: str
    message: str
    created_at: datetime | None = None
