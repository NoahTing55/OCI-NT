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


class AccountUpdate(BaseModel):
    name: str | None = None
    region: str | None = None
    compartment_ocid: str | None = None
    remark: str | None = None
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
class SnipeTaskCreate(BaseModel):
    account_id: int
    region: str
    shape: str = Field(default="VM.Standard.A1.Flex")
    ocpus: float = Field(default=4, gt=0)
    memory_gb: float = Field(default=24, gt=0)
    image_ocid: str = Field(..., description="sourceDetails.imageId")
    subnet_ocid: str = Field(..., description="createVnicDetails.subnetId")
    availability_domain: str
    display_name: str = Field(default="", max_length=128)


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


# ---------------- 批量创建实例 ----------------
class BatchCreateAccountIn(BaseModel):
    """批量创建的账号行：网络配置留空则用任务级默认值（region 留空用账号默认区域）。"""

    account_id: int
    region: str = ""
    image_ocid: str = ""
    subnet_ocid: str = ""
    availability_domain: str = ""


class BatchCreateTaskIn(BaseModel):
    """新建批量创建任务：先选配置、再选账号。

    注意：不硬编码任何 shape 的 OCPU/内存上下限，配错了靠 OCI 400
    返回走 config_error 分类停 item，不在前端/后端做 shape 特判。
    """

    name: str = Field(default="", max_length=128)
    shape: str = Field(default="VM.Standard.E5.Flex")
    ocpus: float = Field(default=2, gt=0)
    memory_gb: float = Field(default=16, gt=0)
    count_per_account: int = Field(default=1, ge=1, le=10, description="每账号创建台数，上限 10 防误操作")
    name_prefix: str = Field(..., min_length=1, max_length=64, description="命名前缀，实例名 = 前缀+序号")
    retry_mode: str = Field(default="direct", description="direct 单次尝试 / retry 失败重试")
    image_ocid: str = Field(default="", description="任务级默认镜像，账号行可覆盖")
    subnet_ocid: str = Field(default="", description="任务级默认子网，账号行可覆盖")
    availability_domain: str = Field(default="", description="任务级默认可用域，账号行可覆盖")
    accounts: list[BatchCreateAccountIn] = Field(..., min_length=1)


class BatchCreateItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    account_id: int
    account_name: str = ""
    region: str
    display_name: str
    status: str
    instance_ocid: str = ""
    attempts: int = 0
    last_error: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BatchCreateTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    shape: str
    ocpus: float | None = None
    memory_gb: float | None = None
    count_per_account: int = 1
    name_prefix: str = ""
    retry_mode: str
    status: str
    total: int = 0
    success_count: int = 0
    fail_count: int = 0
    cancel_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class BatchCreateTaskDetailOut(BatchCreateTaskOut):
    items: list[BatchCreateItemOut] = []


class BatchCreateTemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    shape: str = Field(default="VM.Standard.E5.Flex")
    ocpus: float = Field(default=2, gt=0)
    memory_gb: float = Field(default=16, gt=0)
    count_per_account: int = Field(default=1, ge=1, le=10)
    name_prefix: str = Field(default="", max_length=64)
    retry_mode: str = Field(default="direct")
    image_ocid: str = ""
    subnet_ocid: str = ""
    availability_domain: str = ""
    remark: str = Field(default="", max_length=255)


class BatchCreateTemplateOut(BatchCreateTemplateIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None = None
