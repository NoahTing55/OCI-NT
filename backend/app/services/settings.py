"""系统设置服务：Web 可配项的读写。

设计原则：
- 能 Web 化（本模块管理）：TG_BOT_TOKEN、TG_CHAT_ID、CHECK_DAILY_AT、
  PROXY_SPEEDTEST_MINUTES、SNIPE_LOG_RETENTION_DAYS、INSTANCE_CACHE_TTL、
  JWT_EXPIRE_MINUTES；
- CHECK_INTERVAL_MINUTES 已废弃（保留兼容旧值，不再用于调度、不在前端展示）；
- 必须保留 .env（启动前就需要，DB 不可用）：MASTER_KEY、DATABASE_URL、
  REDIS_URL、JWT_SECRET_KEY、ADMIN_USERNAME/ADMIN_PASSWORD —— 不在本模块管理。

读取优先级：DB 中的值 > 环境变量 > 代码默认值。
- 内存缓存 60 秒，set() 时即时失效；调用方无需重启，改后即时生效。
- SECRET_KEYS 入库前 Fernet 加密；get_all_masked() 对敏感项只返回
  "已设置"/"未设置"，不回明文。
"""
import logging
import os
import time

from app.core.deps import SessionLocal
from app.core.security import decrypt_text, encrypt_text
from app.models.models import SystemSetting

logger = logging.getLogger(__name__)

# Web 可配项定义：
#   group: 前端分区分组；type: str/int/time（决定校验与转换，time 为 "HH:MM"）；
#   secret: 是否敏感（入库加密、API 不回明文）；
#   default: 代码默认值；min: 整数最小值；label/desc: 前端展示；
#   deprecated: 已废弃（保留兼容旧值，不在前端展示）。
WEB_SETTINGS = {
    "TG_BOT_TOKEN": {
        "group": "notify", "type": "str", "secret": True, "default": "",
        "label": "Bot Token",
        "desc": "Telegram Bot Token（找 BotFather 获取）。留空则回退到环境变量 TG_BOT_TOKEN。",
    },
    "TG_CHAT_ID": {
        "group": "notify", "type": "str", "secret": False, "default": "",
        "label": "Chat ID",
        "desc": "接收通知的聊天 ID。留空则回退到环境变量 TG_CHAT_ID。",
    },
    "CHECK_DAILY_AT": {
        "group": "schedule", "type": "time", "secret": False, "default": "08:00",
        "label": "每天存活检查时间",
        "desc": "每天定时执行全量账号存活检查（服务器本地时间）。修改后定时任务自动重排，即时生效。",
    },
    "CHECK_INTERVAL_MINUTES": {
        "group": "schedule", "type": "int", "secret": False, "default": 360, "min": 1,
        "deprecated": True,
        "label": "存活检查间隔（分钟）",
        "desc": "已废弃：存活检查已改为每天定时执行，此项不再生效。",
    },
    "PROXY_SPEEDTEST_MINUTES": {
        "group": "schedule", "type": "int", "secret": False, "default": 30, "min": 1,
        "label": "代理测速间隔（分钟）",
        "desc": "代理延迟测速间隔。修改后定时任务自动重排，即时生效。",
    },
    "SNIPE_LOG_RETENTION_DAYS": {
        "group": "schedule", "type": "int", "secret": False, "default": 7, "min": 1,
        "label": "抢机日志保留（天）",
        "desc": "抢机任务日志只保留近 N 天，过期自动清理。",
    },
    "INSTANCE_CACHE_TTL": {
        "group": "cache", "type": "int", "secret": False, "default": 86400, "min": 0,
        "label": "实例缓存 TTL（秒）",
        "desc": "实例列表 Redis 缓存有效期，默认 86400（24 小时）。0 为关闭缓存，修改后即时生效。",
    },
    "JWT_EXPIRE_MINUTES": {
        "group": "security", "type": "int", "secret": False, "default": 720, "min": 5,
        "label": "登录有效期（分钟）",
        "desc": "JWT Token 有效期（默认 12 小时）。只影响新签发的 Token，已签发的不变。",
    },
}

# 入库前必须 Fernet 加密的键
SECRET_KEYS = {"TG_BOT_TOKEN"}

# 前端分区分组展示名（按 WEB_SETTINGS 定义顺序展示）
GROUP_NAMES = {
    "notify": "Telegram 通知",
    "schedule": "定时任务",
    "cache": "缓存",
    "security": "登录安全",
}

_CACHE_TTL_SEC = 60  # 内存缓存有效期（秒）
_cache: dict = {}  # key -> (value, expire_ts)


def _parse_hhmm(raw) -> tuple:
    """解析 "HH:MM" 时间字符串，返回 (hour, minute)。非法抛 ValueError。"""
    import re
    s = str(raw or "").strip()
    m = re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", s)
    if not m:
        raise ValueError(f"时间格式非法，应为 HH:MM（如 08:00）， got: {raw!r}")
    return int(m.group(1)), int(m.group(2))


def _coerce(key: str, raw) -> object:
    """按 WEB_SETTINGS 的 type 把值转成目标类型。"""
    spec = WEB_SETTINGS[key]
    if spec["type"] == "int":
        return int(str(raw).strip())
    if spec["type"] == "time":
        # 校验格式；非法时抛 ValueError，get_setting 会回退代码默认值
        _parse_hhmm(raw)
        return str(raw).strip()
    return str(raw)


def _validate(key: str, value) -> str:
    """校验并返回规范化后的字符串（入库/比较用）。非法抛 ValueError。"""
    spec = WEB_SETTINGS[key]
    if spec["type"] == "int":
        try:
            iv = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{spec['label']}必须是整数")
        if iv < spec.get("min", 0):
            raise ValueError(f"{spec['label']}不能小于 {spec['min']}")
        return str(iv)
    if spec["type"] == "time":
        try:
            _parse_hhmm(value)
        except ValueError:
            raise ValueError(f"{spec['label']}格式非法，应为 HH:MM（如 08:00）")
        return str(value).strip()
    return str(value or "").strip()


def _read_db(key: str) -> str | None:
    """从 DB 读原始值（secret 自动解密）。

    不存在/空值返回 None（回退到环境变量）。表不存在等 DB 异常也返回 None，
    设置读取永不让业务崩溃（比如旧库还没执行 0005 迁移时）。
    """
    from sqlalchemy.exc import SQLAlchemyError

    db = SessionLocal()
    try:
        row = db.get(SystemSetting, key)
        if row is None:
            return None
        raw = (row.value_encrypted or "").strip()
        if not raw:
            return None
        if key in SECRET_KEYS:
            try:
                return decrypt_text(raw)
            except ValueError:
                logger.error("系统设置 %s 解密失败（MASTER_KEY 可能已更换），回退到环境变量", key)
                return None
        return raw
    except SQLAlchemyError as e:
        logger.warning("读取系统设置 %s 失败，回退到环境变量：%s", key, e)
        return None
    finally:
        db.close()


def invalidate_cache(key: str | None = None) -> None:
    """失效内存缓存；key 为 None 时全清。set() 后自动调用。"""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


def get_setting(key: str):
    """按优先级解析设置值：DB > 环境变量 > 代码默认值。

    带 60 秒内存缓存；未知 key 抛 KeyError；非法值记日志并回退默认值。
    """
    if key not in WEB_SETTINGS:
        raise KeyError(f"未知设置项：{key}")
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and hit[1] > now:
        return hit[0]
    # 1. DB
    val = _read_db(key)
    # 2. 环境变量（非空才算设置）
    if val is None:
        env_val = os.environ.get(key)
        if env_val is not None and env_val.strip() != "":
            val = env_val
    # 3. 代码默认值
    if val is None:
        val = WEB_SETTINGS[key]["default"]
    try:
        coerced = _coerce(key, val)
    except (TypeError, ValueError):
        logger.warning("设置项 %s 的值 %r 非法，回退代码默认值", key, val)
        coerced = WEB_SETTINGS[key]["default"]
    _cache[key] = (coerced, now + _CACHE_TTL_SEC)
    return coerced


def set_setting(key: str, value) -> bool:
    """校验并写入 DB（secret 加密存储），失效缓存。返回是否有变化。"""
    if key not in WEB_SETTINGS:
        raise KeyError(f"未知设置项：{key}")
    normalized = _validate(key, value)
    old = _read_db(key) or ""
    # 读 DB 做新旧比较时也走规范化，保证 "30" 与 30 视为相同
    try:
        old_norm = _validate(key, old) if old else ""
    except ValueError:
        old_norm = old
    changed = old_norm != normalized
    stored = encrypt_text(normalized) if (key in SECRET_KEYS and normalized) else normalized
    db = SessionLocal()
    try:
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key, value_encrypted=stored, is_secret=key in SECRET_KEYS)
            db.add(row)
        else:
            row.value_encrypted = stored
            row.is_secret = key in SECRET_KEYS
        db.commit()
    finally:
        db.close()
    invalidate_cache(key)
    return changed


def is_set(key: str) -> bool:
    """该设置项是否有有效值（DB 或环境变量）。"""
    if key not in WEB_SETTINGS:
        raise KeyError(f"未知设置项：{key}")
    if _read_db(key) is not None:
        return True
    return bool(os.environ.get(key, "").strip())


def get_all_masked() -> list:
    """按分组返回全部设置项（API 用）。

    secret 项只返回是否已设置（value 为"已设置"/""），不回明文；
    非 secret 项返回解析后的实际值（含 DB/环境变量/默认值）。
    """
    groups: dict = {}
    for key, spec in WEB_SETTINGS.items():
        if spec.get("deprecated"):
            continue  # 已废弃项不在前端展示
        group = spec["group"]
        if spec["secret"]:
            value = "已设置" if is_set(key) else ""
        else:
            value = get_setting(key)
        groups.setdefault(group, []).append(
            {
                "key": key,
                "label": spec["label"],
                "desc": spec.get("desc", ""),
                "type": spec["type"],
                "secret": spec["secret"],
                "is_set": is_set(key),
                "value": value,
                "min": spec.get("min"),
            }
        )
    return [
        {"key": g, "name": GROUP_NAMES.get(g, g), "items": items}
        for g, items in groups.items()
    ]
