"""账号列表新字段：created_at / instance_count / snipe_task_status。

运行：/tmp/ocitest/bin/python backend/tests/test_account_list_fields.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_acct_fields.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:////tmp/test_acct_fields.db"
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal  # noqa: E402
from app.models.models import Account, Base, SnipeTask  # noqa: E402
from app.api.accounts import _instance_counts_from_cache, _snipe_status_map  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


from app.core.deps import engine  # noqa: E402
Base.metadata.create_all(bind=engine)

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


def make_account(db, name="测试账号"):
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
    a1 = make_account(db, "账号A")
    a2 = make_account(db, "账号B")

    # 抢机任务状态：A 有 running，B 有 paused
    db.add(SnipeTask(account_id=a1.id, region="ap-seoul-1", status="running"))
    db.add(SnipeTask(account_id=a2.id, region="ap-seoul-1", status="paused"))
    db.commit()

    # 1. _snipe_status_map
    sm = _snipe_status_map(db)
    check("running 任务映射", sm.get(a1.id) == "running", sm)
    check("paused 任务映射", sm.get(a2.id) == "paused", sm)
    check("无任务账号不在映射中", 99999 not in sm)

    # 2. _instance_counts_from_cache：无 Redis 时返回空 dict 不抛异常
    counts = _instance_counts_from_cache()
    check("无缓存返回 dict", isinstance(counts, dict), type(counts))

    # 3. GET /api/accounts 返回新字段
    r = client.get("/api/accounts", headers=auth)
    check("列表 200", r.status_code == 200, r.text[:150])
    data = r.json()
    check("返回 2 个账号", len(data) == 2, len(data))
    by_name = {x["name"]: x for x in data}
    check("created_at 透出", by_name["账号A"].get("created_at") is not None)
    check("instance_count 默认 0", by_name["账号A"].get("instance_count") == 0)
    check("snipe running", by_name["账号A"].get("snipe_task_status") == "running")
    check("snipe paused", by_name["账号B"].get("snipe_task_status") == "paused")

    # 4. running 优先于 paused（同一账号两个任务）
    db.add(SnipeTask(account_id=a2.id, region="ap-seoul-1", status="running"))
    db.commit()
    sm2 = _snipe_status_map(db)
    check("running 优先于 paused", sm2.get(a2.id) == "running", sm2)

    db.close()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
