"""系统设置 Web 化测试：读写优先级、secret 脱敏、校验、TG 测试、定时重排。

运行：/tmp/settings_test/bin/python backend/tests/test_settings.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_settings.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"]="sqlite:///" + "/tmp/test_settings.db"
os.environ.pop("CHECK_INTERVAL_MINUTES", None)
os.environ.pop("TG_BOT_TOKEN", None)
os.environ.pop("TG_CHAT_ID", None)

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core import telegram as tg_module  # noqa: E402
from app.core.deps import SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models.models import Base, SystemSetting  # noqa: E402
from app.services.settings import (  # noqa: E402
    WEB_SETTINGS,
    get_all_masked,
    get_setting,
    invalidate_cache,
    is_set,
    set_setting,
)

PASS = []
FAIL = []


# 测试库建表（含 system_settings）
Base.metadata.create_all(bind=engine)


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(
        ("PASS " if cond else "FAIL ") + name
        + (" | " + str(extra) if extra and not cond else "")
    )


def fresh():
    invalidate_cache()


# ---------- 优先级：DB > 环境变量 > 代码默认值 ----------
fresh()
check("默认无配置时返回代码默认值", get_setting("CHECK_INTERVAL_MINUTES") == 360)

os.environ["CHECK_INTERVAL_MINUTES"] = "120"
fresh()
check("环境变量覆盖默认值", get_setting("CHECK_INTERVAL_MINUTES") == 120)

set_setting("CHECK_INTERVAL_MINUTES", 45)
fresh()
check("DB 覆盖环境变量", get_setting("CHECK_INTERVAL_MINUTES") == 45)
del os.environ["CHECK_INTERVAL_MINUTES"]
fresh()
check("删环境变量后 DB 仍生效", get_setting("CHECK_INTERVAL_MINUTES") == 45)

# ---------- secret 加密存储与脱敏 ----------
set_setting("TG_BOT_TOKEN", "secret-token-123")
db = SessionLocal()
row = db.get(SystemSetting, "TG_BOT_TOKEN")
check("DB 中无明文", row is not None and row.value_encrypted != "secret-token-123")
check("is_secret 标记", row.is_secret is True)
db.close()
fresh()
check("读回解密正确", get_setting("TG_BOT_TOKEN") == "secret-token-123")

masked = get_all_masked()
notify = next(g for g in masked if g["key"] == "notify")
token_item = next(i for i in notify["items"] if i["key"] == "TG_BOT_TOKEN")
check("secret 不回明文", token_item["value"] == "已设置" and token_item["is_set"] is True)
chat_item = next(i for i in notify["items"] if i["key"] == "TG_CHAT_ID")
check("未设置的 secret 显示为空", chat_item["value"] == "" and chat_item["is_set"] is False)

# ---------- 校验 ----------
try:
    set_setting("CHECK_INTERVAL_MINUTES", "abc")
    check("非法整数抛 ValueError", False)
except ValueError:
    check("非法整数抛 ValueError", True)
try:
    set_setting("CHECK_INTERVAL_MINUTES", 0)
    check("小于最小值抛 ValueError", False)
except ValueError:
    check("小于最小值抛 ValueError", True)
try:
    set_setting("NO_SUCH_KEY", 1)
    check("未知 key 抛 KeyError", False)
except KeyError:
    check("未知 key 抛 KeyError", True)

check("重复设相同值返回未变更", set_setting("CHECK_INTERVAL_MINUTES", 45) is False)
check("改值返回已变更", set_setting("CHECK_INTERVAL_MINUTES", 46) is True)

# ---------- JWT 有效期动态读取 ----------
set_setting("JWT_EXPIRE_MINUTES", 60)
fresh()
import time as _time  # noqa: E402

import jwt as pyjwt  # noqa: E402

_before = int(_time.time())
tok = create_access_token("u1")
payload = pyjwt.decode(tok, options={"verify_signature": False})
check(
    "JWT 有效期取网页设置",
    abs(payload["exp"] - (_before + 3600)) <= 5,
    payload["exp"] - _before,
)

# ---------- API 测试 ----------
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=__import__("app.core.deps", fromlist=["engine"]).engine)

with TestClient(app) as client:
    r = client.get("/api/settings")
    check("未登录 GET /api/settings 401", r.status_code == 401, r.status_code)

    r = client.post("/api/auth/init", json={"username": "admin", "password": "admin123"})
    check("初始化管理员", r.status_code == 200, r.text[:100])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    token = r.json()["access_token"]
    auth = {"Authorization": "Bearer " + token}

    r = client.get("/api/settings", headers=auth)
    check("GET /api/settings 200 且分组返回", r.status_code == 200 and len(r.json()["groups"]) == 4, r.status_code)
    groups = {g["key"]: g for g in r.json()["groups"]}
    check("TG token 仍脱敏", groups["notify"]["items"][0]["value"] == "已设置")

    r = client.put("/api/settings", headers=auth, json={"NO_SUCH_KEY": 1})
    check("未知 key 400", r.status_code == 400, r.text[:100])
    r = client.put("/api/settings", headers=auth, json={"CHECK_INTERVAL_MINUTES": "abc"})
    check("非法值 400", r.status_code == 400, r.text[:100])
    r = client.put("/api/settings", headers=auth, json={"CHECK_INTERVAL_MINUTES": 0})
    check("小于最小值 400", r.status_code == 400, r.text[:100])

    r = client.put("/api/settings", headers=auth, json={"CHECK_INTERVAL_MINUTES": 90, "INSTANCE_CACHE_TTL": 120})
    body = r.json()
    check("合法更新 200", r.status_code == 200 and body["ok"], r.text[:150])
    check("changed 含两项", set(body["changed"]) == {"CHECK_INTERVAL_MINUTES", "INSTANCE_CACHE_TTL"}, body.get("changed"))
    check("定时任务自动重排", body["rescheduled"].get("check_all_accounts") == 90, body.get("rescheduled"))

    # secret 留空 = 不修改
    r = client.put("/api/settings", headers=auth, json={"TG_BOT_TOKEN": ""})
    check("secret 留空不修改", r.status_code == 200 and r.json()["changed"] == [], r.text[:150])
    fresh()
    check("token 仍为旧值", get_setting("TG_BOT_TOKEN") == "secret-token-123")

    # test-telegram：未配置
    set_setting("TG_BOT_TOKEN", "")
    set_setting("TG_CHAT_ID", "")
    fresh()
    r = client.post("/api/settings/test-telegram", headers=auth)
    check("未配置时测试 400 并给原因", r.status_code == 400 and "未配置" in r.json()["detail"], r.text[:120])

    # test-telegram：mock 底层发送成功
    async def fake_post(token, chat_id, text):
        assert token == "dummy" and chat_id == "123"
        return True, ""

    tg_module._post_message = fake_post
    os.environ["TG_BOT_TOKEN"] = "dummy"
    os.environ["TG_CHAT_ID"] = "123"
    fresh()
    r = client.post("/api/settings/test-telegram", headers=auth)
    check("mock 发送成功 200", r.status_code == 200 and r.json()["ok"], r.text[:120])
    del os.environ["TG_BOT_TOKEN"]
    del os.environ["TG_CHAT_ID"]
    fresh()

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)
