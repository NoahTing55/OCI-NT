"""账号成本：accounts.cost 列 + AccountUpdate.cost + 列表透出。

运行：/tmp/ocitest/bin/python backend/tests/test_account_cost.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_acct_cost.db"
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


def make_account(db, name="成本账号"):
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

    # 1. 默认 cost 为 0
    check("cost 默认 0", (a1.cost or 0) == 0, a1.cost)

    # 2. PUT 更新 cost
    r = client.put(f"/api/accounts/{a1.id}", json={"cost": 12.5}, headers=auth)
    check("PUT cost 200", r.status_code == 200, r.text[:150])
    check("返回 cost 12.5", r.json().get("cost") == 12.5, r.json().get("cost"))

    # 3. 列表透出 cost
    r = client.get("/api/accounts", headers=auth)
    check("列表 200", r.status_code == 200, r.text[:150])
    row = [x for x in r.json() if x["id"] == a1.id][0]
    check("列表 cost 透出", row.get("cost") == 12.5, row.get("cost"))

    # 4. 更新为 0
    r = client.put(f"/api/accounts/{a1.id}", json={"cost": 0}, headers=auth)
    check("cost 置 0 成功", r.status_code == 200 and r.json().get("cost") == 0, r.text[:150])

    # 5. 不传 cost 不影响原有值
    r = client.put(f"/api/accounts/{a1.id}", json={"cost": 7.25}, headers=auth)
    assert r.status_code == 200
    r = client.put(f"/api/accounts/{a1.id}", json={"remark": "只改备注"}, headers=auth)
    check("不传 cost 保持原值", r.status_code == 200 and r.json().get("cost") == 7.25, r.json().get("cost"))

    # 6. 不存在的账号 404
    r = client.put("/api/accounts/99999", json={"cost": 1}, headers=auth)
    check("不存在账号 404", r.status_code == 404, r.status_code)

    db.close()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
