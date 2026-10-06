"""全局配置：从环境变量 / .env 读取。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "OCI 多账号管理面板"

    # 主密钥：用于 Fernet 加密账号私钥 / 代理密码 / CF Token
    # 生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # 警告：丢失后所有已加密数据无法解密
    MASTER_KEY: str = ""

    DATABASE_URL: str = "postgresql+psycopg2://oci:oci@postgres:5432/ocipanel"
    REDIS_URL: str = "redis://redis:6379/0"

    # Telegram Bot（通知渠道只保留 TG）
    TG_BOT_TOKEN: str = ""
    TG_CHAT_ID: str = ""

    # 全量存活检查间隔（分钟）
    CHECK_INTERVAL_MINUTES: int = 360

    # 代理测速间隔（分钟）
    PROXY_SPEEDTEST_MINUTES: int = 30

    # 抢机日志保留天数（SnipeLog 定期清理只保留近 N 天）
    SNIPE_LOG_RETENTION_DAYS: int = 7

    # CORS 允许的前端地址（本地开发用；docker 内走 vite 代理，不需要 CORS）
    CORS_ORIGINS: str = "http://localhost:5173"

    # 登录鉴权
    JWT_SECRET_KEY: str = ""  # 为空则用 MASTER_KEY；建议单独设置
    JWT_EXPIRE_MINUTES: int = 720  # Token 有效期（分钟），默认 12 小时
    ADMIN_USERNAME: str = ""  # 首次启动时若无任何账号则自动创建管理员
    ADMIN_PASSWORD: str = ""  # 创建后请立即登录改密码

    # 实例列表 Redis 缓存 TTL（秒），0 为关闭缓存
    INSTANCE_CACHE_TTL: int = 60

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
