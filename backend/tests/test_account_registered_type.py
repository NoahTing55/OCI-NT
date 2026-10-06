"""账号真实注册时间 + 账号类型：registered_at / account_type。

运行：/tmp/ocitest/bin/python backend/tests/test_account_registered_type.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import asyncio
import os
import sys
from datetime import datetime

DB_PATH = "/tmp/test_acct_regtype.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal  # noqa: E402
from app.models.models import Account, Base  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


from app.core.deps import engine  # noqa: E402
Base.metadata.create_all(bind=engine)

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


def make_account(db, name="注册账号"):
    from app.core.security import encrypt_text
    a = Account(
        name=name,
        tenancy_ocid="ocid1.tenancy.oc1..test",
        user_ocid="ocid1.user.oc1..test",
        fingerprint="aa:bb:cc",
        private_key_enc=encrypt_text(FAKE_KEY),
        region="ap-seoul-1",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


with TestClient(app := __import__("app.main", fromlist=["app"]).app) as client:
    r = client.post("/api/auth/init", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text[:150]
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text[:150]
    auth = {"Authorization": "Bearer " + r.json()["access_token"]}

    db = SessionLocal()
    a1 = make_account(db)

    # 1. 新字段默认值
    check("registered_at 默认 None", a1.registered_at is None, a1.registered_at)
    check("account_type 默认空", (a1.account_type or "") == "", a1.account_type)

    # 2. PUT 设置 registered_at
    r = client.put(f"/api/accounts/{a1.id}", json={"registered_at": "2024-03-15T00:00:00"}, headers=auth)
    check("PUT registered_at 200", r.status_code == 200, r.text[:150])
    got = r.json().get("registered_at")
    check("返回 registered_at", got and got.startswith("2024-03-15"), got)

    # 3. 列表透出新字段
    r = client.get("/api/accounts", headers=auth)
    row = [x for x in r.json() if x["id"] == a1.id][0]
    check("列表透出 registered_at", (row.get("registered_at") or "").startswith("2024-03-15"), row.get("registered_at"))
    check("列表透出 account_type", "account_type" in row, row.get("account_type"))

    # 4. 显式传 null 清空 registered_at
    r = client.put(f"/api/accounts/{a1.id}", json={"registered_at": None}, headers=auth)
    check("清空 registered_at 200", r.status_code == 200 and r.json().get("registered_at") is None, r.text[:150])

    # 5. 不传 registered_at 不影响原有值
    r = client.put(f"/api/accounts/{a1.id}", json={"registered_at": "2023-01-01T00:00:00"}, headers=auth)
    assert r.status_code == 200
    r = client.put(f"/api/accounts/{a1.id}", json={"remark": "只改备注"}, headers=auth)
    check("不传 registered_at 保持原值",
          r.status_code == 200 and (r.json().get("registered_at") or "").startswith("2023-01-01"),
          r.json().get("registered_at"))

    # 6. PUT 更新 account_type
    r = client.put(f"/api/accounts/{a1.id}", json={"account_type": "free"}, headers=auth)
    check("PUT account_type 200", r.status_code == 200 and r.json().get("account_type") == "free", r.text[:150])

    db.close()

# 7. OciClient.get_subscription_type：mock 各种返回
from app.core.oci_client import OciClient  # noqa: E402


class FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def make_client(tier=None, status=200, empty=False):
    c = OciClient.__new__(OciClient)
    c.tenancy_ocid = "ocid1.tenancy.oc1..x"
    c.region = "ap-seoul-1"
    data = [] if empty else ([{"subscriptionTier": tier}] if tier else [{}])

    async def fake_request(method, service, path, json_body=None):
        assert service == "identity", service
        assert "ospHomeRegion=" in path, path
        return FakeResp(status, data)

    c.request = fake_request
    return c


async def run_sub_tests():
    check("ALWAYS_FREE → free",
          await make_client("ALWAYS_FREE").get_subscription_type() == "free")
    check("FREE → free",
          await make_client("FREE").get_subscription_type() == "free")
    check("PAID → paid",
          await make_client("PAID").get_subscription_type() == "paid")
    check("未知 tier → None",
          await make_client("WEIRD").get_subscription_type() is None)
    check("空订阅列表 → None",
          await make_client(empty=True).get_subscription_type() is None)
    check("非 200 → None",
          await make_client("PAID", status=403).get_subscription_type() is None)

    # 网络异常不抛
    c = make_client("PAID")

    async def boom(method, service, path, json_body=None):
        raise RuntimeError("boom")

    c.request = boom
    check("异常 → None 不抛", await c.get_subscription_type() is None)


asyncio.run(run_sub_tests())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
