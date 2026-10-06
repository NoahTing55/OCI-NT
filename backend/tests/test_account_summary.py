"""账户摘要测试：Limits API 容错 + 摘要接口结构。

运行：/tmp/ocitest/bin/python backend/tests/test_account_summary.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

DB_PATH = "/tmp/test_summary.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.api import account_summary  # noqa: E402
from app.core.deps import SessionLocal, engine  # noqa: E402
from app.core.security import encrypt_text  # noqa: E402
from app.models.models import Account, Base  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


# ---------- 1. get_compute_quota 容错 ----------
from app.core.oci_client import OciClient  # noqa: E402


async def _quota_fail_cases():
    # 构造一个不发起真实请求的对象：直接 mock request 方法
    c = OciClient.__new__(OciClient)
    c.region = "ap-seoul-1"

    class FakeResp:
        status_code = 500

        def json(self):
            return {}

    async def fake_request(*a, **k):
        return FakeResp()

    c.request = fake_request
    r = await c.get_compute_quota("ocid1.tenancy.oc1..x", "standard-a1-core-count")
    check("配额 500 返回 None 不抛异常", r == {"available": None, "used": None}, str(r))

    async def fake_raise(*a, **k):
        raise ConnectionError("boom")

    c.request = fake_raise
    r = await c.get_compute_quota("ocid1.tenancy.oc1..x", "standard-a1-core-count")
    check("配额异常返回 None 不抛异常", r == {"available": None, "used": None}, str(r))

    class FakeResp200:
        status_code = 200

        def json(self):
            return {"available": 96, "used": 4}

    async def fake_ok(*a, **k):
        # 顺带验证走的是 limits 服务 + compute 配额端点（host 由 request() 按 service 拼接）
        assert a[1] == "limits", a[1]
        assert "/20181004/services/compute/limits/standard-a1-core-count" in a[2], a[2]
        return FakeResp200()

    c.request = fake_ok
    r = await c.get_compute_quota("ocid1.tenancy.oc1..x", "standard-a1-core-count")
    check("配额 200 解析 available/used", r == {"available": 96, "used": 4}, str(r))


asyncio.run(_quota_fail_cases())

# ---------- 2. 摘要接口 ----------
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=engine)


def _make_account():
    db = SessionLocal()
    try:
        acc = Account(
            name="摘要测试",
            tenancy_ocid="ocid1.tenancy.oc1..test",
            user_ocid="ocid1.user.oc1..test",
            fingerprint="aa:bb:cc",
            private_key_enc=encrypt_text("fake"),
            region="ap-seoul-1",
        )
        db.add(acc)
        db.commit()
        return acc.id
    finally:
        db.close()


with TestClient(app) as client:
    from app.core.security import create_access_token, hash_password
    from app.models.models import Operator

    _db = SessionLocal()
    _db.add(Operator(username="tester", password_hash=hash_password("test123")))
    _db.commit()
    _db.close()
    client.headers.update({"Authorization": "Bearer " + create_access_token("tester")})

    account_id = _make_account()

    fake_items = [
        {
            "account_id": account_id, "lifecycle_state": "RUNNING",
            "shape_config": {"ocpus": 4.0, "memoryInGBs": 24.0},
        },
        {
            "account_id": account_id, "lifecycle_state": "STOPPED",
            "shape_config": {"ocpus": 1.0, "memoryInGBs": 1.0},
        },
        {
            "account_id": 9999, "lifecycle_state": "RUNNING",  # 其他账号的，不应计入
            "shape_config": {"ocpus": 2.0, "memoryInGBs": 12.0},
        },
    ]
    fake_quotas = {
        "e2": {"available": 98, "used": 2},
        "a1": {"available": None, "used": None},
        "e5": {"available": 0, "used": 0},
    }

    async def fake_cached(accounts, filters=None):
        return fake_items, []

    async def fake_quotas_fn(account):
        return dict(fake_quotas)

    with patch("app.api.account_summary.instance_service.fetch_all_instances_cached", fake_cached), \
         patch("app.api.account_summary._fetch_quotas", fake_quotas_fn):
        r = client.get("/api/account-summary")
        check("GET /api/account-summary 200", r.status_code == 200, r.text[:120])
        data = r.json()
        check("返回数组且含测试账号", isinstance(data, list) and len(data) == 1, str(len(data) if isinstance(data, list) else data))
        s = data[0]
        check("实例总数 2", s["instance_count"] == 2, str(s["instance_count"]))
        check("运行中 1", s["running_count"] == 1, str(s["running_count"]))
        check("OCPU 只计 RUNNING（4.0）", s["ocpu_used"] == 4.0, str(s["ocpu_used"]))
        check("内存只计 RUNNING（24.0）", s["memory_used_gb"] == 24.0, str(s["memory_used_gb"]))
        check("配额 e2/a1/e5 三项齐全", set(s["quotas"].keys()) == {"e2", "a1", "e5"}, str(s["quotas"].keys()))
        check("配额失败项为 null", s["quotas"]["a1"] == {"available": None, "used": None}, str(s["quotas"]["a1"]))
        check("字段含 name/region", s["name"] == "摘要测试" and s["region"] == "ap-seoul-1")

    # 空账号时返回 []
    _db2 = SessionLocal()
    _db2.query(Account).delete()
    _db2.commit()
    _db2.close()
    with patch("app.api.account_summary.instance_service.fetch_all_instances_cached", fake_cached):
        r = client.get("/api/account-summary")
        check("无账号返回空数组", r.status_code == 200 and r.json() == [], r.text[:80])

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)
