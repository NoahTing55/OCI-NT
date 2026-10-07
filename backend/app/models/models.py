"""SQLAlchemy 数据模型。"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _now():
    return datetime.utcnow()


class Account(Base):
    """OCI 账号。

    - private_key_enc：Fernet 加密后的 PEM 私钥，永不返回给前端；
    - proxy_id 唯一约束：保证「一账号一代理」（单API单代理）；
    - status：unchecked 未检查 / healthy 正常 / key_invalid 密钥失效 /
      forbidden 权限不足 / not_found 用户不存在 /
      network_error 网络或代理异常 / unknown_error 未知异常。
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    tenancy_ocid = Column(String(255), nullable=False)
    user_ocid = Column(String(255), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    private_key_enc = Column(Text, nullable=False)
    region = Column(String(64), nullable=False, default="ap-seoul-1")
    # 实例列表查询用的 compartment OCID；为空则用 tenancy_ocid（根 compartment）
    compartment_ocid = Column(String(255), default="")
    proxy_id = Column(Integer, ForeignKey("proxies.id", ondelete="SET NULL"), unique=True, nullable=True)
    status = Column(String(32), nullable=False, default="unchecked")
    last_check_at = Column(DateTime, nullable=True)
    remark = Column(String(255), default="")
    cost = Column(Float, default=0)  # 账号成本（OCI-Start 式，可点击修改）
    # 账号真实注册时间（可空；为空时存活天数用 created_at 兜底）
    registered_at = Column(DateTime, nullable=True)
    # 账号类型：free 免费 / paid 付费 / 空 未知（存活检查时调 Subscription API 更新）
    account_type = Column(String(32), default="")
    # 租户名称（存活检查时调 tenancies 接口获取，可空）
    tenancy_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    proxy = relationship("Proxy", back_populates="account", uselist=False)


class Proxy(Base):
    """代理：一个代理同一时间最多被一个账号绑定。"""

    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    scheme = Column(String(16), nullable=False, default="http")  # http / socks5
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(128), default="")
    password_enc = Column(Text, default="")  # Fernet 加密，可空
    status = Column(String(32), default="unchecked")  # unchecked / ok / fail
    latency_ms = Column(Integer, nullable=True)
    remark = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    account = relationship("Account", back_populates="proxy", uselist=False)


class SnipeTask(Base):
    """抢机任务。

    状态机：pending（新建未启动）/ running（抢机中）/ paused（已暂停）/
    success（抢到）/ stopped（手动停止）/ failed（配置错误/密钥失效/连续未知错误）。
    同一账号同一 shape 同时只允许一个 running/paused 任务（API 层校验）。
    """

    __tablename__ = "snipe_tasks"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    region = Column(String(64), nullable=False)
    shape = Column(String(64), nullable=False, default="VM.Standard.A1.Flex")
    ocpus = Column(Float, default=4)
    memory_gb = Column(Float, default=24)
    image_ocid = Column(String(255), default="")  # image_id：sourceDetails.imageId
    subnet_ocid = Column(String(255), default="")  # createVnicDetails.subnetId
    availability_domain = Column(String(255), default="")
    display_name = Column(String(128), default="")  # 新实例显示名，空则自动生成
    root_password = Column(String(128), default="")  # cloud-init 设置的 root 密码（明文，参考 OCI-Start）
    target_count = Column(Integer, default=1)  # 目标抢机台数（一次抢 N 台）
    success_count = Column(Integer, default=0)  # 已抢到台数
    interval_seconds = Column(Integer, default=60)  # 无容量时重试间隔（秒），默认 60
    open_all_ports = Column(Boolean, default=True)  # 开机后是否放行所有端口（安全列表加全端口规则），默认 True
    boot_volume_gb = Column(Integer, default=50)  # 启动卷大小（GB），OCI 限制 50-16384，默认 50
    status = Column(String(32), default="pending")  # pending / running / paused / success / stopped / failed
    attempts = Column(Integer, default=0)
    last_error = Column(Text, default="")
    instance_ocid = Column(String(255), default="")  # 抢到后记录实例 OCID
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    account = relationship("Account")


class SnipeLog(Base):
    """抢机任务日志：每个任务独立日志流，前端轮询查看。

    只保留近 N 天（SNIPE_LOG_RETENTION_DAYS，默认 7 天），由 SniperManager
    的清理协程定期删除过期日志。
    """

    __tablename__ = "snipe_logs"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("snipe_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    level = Column(String(16), default="info")  # info / warning / error
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now, index=True)


class BatchTask(Base):
    """TODO(M2)：批量任务（开机 / 关机 / 重启 / 终止）。"""

    __tablename__ = "batch_tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), default="")
    task_type = Column(String(32), nullable=False)  # power_on / power_off / reboot / terminate
    status = Column(String(32), default="pending")  # pending / running / done / failed
    total = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    detail_json = Column(Text, default="{}")  # 每个实例的执行明细
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class DomainBinding(Base):
    """实例-域名映射，Cloudflare DNS 自动同步用。"""

    __tablename__ = "domain_bindings"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)
    instance_ocid = Column(String(255), default="")
    instance_name = Column(String(128), default="")
    domain = Column(String(255), nullable=False)
    record_type = Column(String(8), default="A")  # A / AAAA
    zone_id = Column(String(64), default="")
    record_id = Column(String(64), default="")
    proxied = Column(Boolean, default=False)  # CF 小黄云
    auto_sync = Column(Boolean, default=True)  # 换 IP 后自动同步
    ttl = Column(Integer, default=120)  # DNS TTL（秒）
    cf_token_id = Column(Integer, ForeignKey("cloudflare_tokens.id", ondelete="SET NULL"), nullable=True)
    last_ip = Column(String(64), default="")
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (UniqueConstraint("domain", "record_type", name="uq_domain_record"),)


class CloudflareToken(Base):
    """Cloudflare API Token：Fernet 加密存储，权限最小化（Zone.DNS 编辑），永不返回给前端。"""

    __tablename__ = "cloudflare_tokens"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    token_enc = Column(Text, nullable=False)
    remark = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)


class OperationLog(Base):
    """操作审计日志。"""

    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True)
    action = Column(String(64), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    detail = Column(Text, default="")
    operator = Column(String(64), default="web")
    created_at = Column(DateTime, default=_now)


class Operator(Base):
    """面板登录账号（本地鉴权）。

    - password_hash：pbkdf2_sha256 存储，见 core/security；
    - totp_secret_enc：Fernet 加密的 TOTP 密钥，未启用/未绑定时为空；
    - totp_enabled：是否启用双因素。
    """

    __tablename__ = "operators"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    totp_secret_enc = Column(Text, default="")
    totp_enabled = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class SystemSetting(Base):
    """系统设置（Web 可配项，见 services/settings.py 的 WEB_SETTINGS）。

    - key：设置键（主键）；
    - value_encrypted：SECRET_KEYS 中的键用 Fernet 加密存储，其余明文；
    - is_secret：敏感项（Token 类），API 返回时只给"已设置/未设置"，不回明文。
    读取优先级：DB 中的值 > 环境变量 > 代码默认值。
    """

    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True)
    value_encrypted = Column(Text, default="")
    is_secret = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
