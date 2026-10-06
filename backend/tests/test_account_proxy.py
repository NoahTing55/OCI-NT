"""新建账号时直接绑定代理：proxy_id 参数 + 占用检查 + 代理列表绑定状态。

运行：/tmp/ocitest/bin/python backend/tests/test_account_proxy.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_acct_proxy.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal, engine  # noqa: E402
from app.core.security import encrypt_text  # noqa: E402
from app.models.models import Account, Base, Proxy  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


Base.metadata.create_all(bind=engine)

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


def make_account_payload(name="测试账号"):
    return {
        "name": name,
        "tenancy_ocid": "ocid1.tenancy.oc1..test",
        "user_ocid": "ocid1.user.oc1..test",
        "fingerprint": "aa:bb:cc",
        "private_key": FAKE_KEY,
        "region": "ap-seoul-1",
    }


with TestClient(app := __import__("app.main", fromlist=["app"]).app) as client:
    # 初始化管理员并登录（接口带鉴权）
    r = client.post("/api/auth/init", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text[:150]
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text[:150]
    auth = {"Authorization": "Bearer " + r.json()["access_token"]}

    db = SessionLocal()
    # 建两个代理
    p1 = Proxy(name="代理1", scheme="socks5", host="1.2.3.4", port=1080)
    p2 = Proxy(name="代理2", scheme="http", host="5.6.7.8", port=8080)
    db.add_all([p1, p2])
    db.commit()
    p1_id, p2_id = p1.id, p2.id
    db.close()

    # 1. 不带 proxy_id 创建：直连
    r = client.post("/api/accounts", headers=auth, json=make_account_payload("账号A"))
    check("不带 proxy_id 创建 200", r.status_code == 200, r.text[:150])
    a_id = r.json()["id"]
    db = SessionLocal()
    a = db.get(Account, a_id)
    check("proxy_id 为 None（直连）", a.proxy_id is None)
    db.close()

    # 2. 带 proxy_id 创建：绑定成功
    payload = make_account_payload("账号B")
    payload["proxy_id"] = p1_id
    r = client.post("/api/accounts", headers=auth, json=payload)
    check("带 proxy_id 创建 200", r.status_code == 200, r.text[:150])
    check("返回的 proxy_id 正确", r.json().get("proxy_id") == p1_id, r.text[:150])

    # 3. 再用同一个代理创建：400 报错
    payload = make_account_payload("账号C")
    payload["proxy_id"] = p1_id
    r = client.post("/api/accounts", headers=auth, json=payload)
    check("占用代理创建 400", r.status_code == 400, r.text[:150])
    check("错误信息提到已绑定", "绑定" in r.text)

    # 4. 不存在的代理：404
    payload = make_account_payload("账号D")
    payload["proxy_id"] = 99999
    r = client.post("/api/accounts", headers=auth, json=payload)
    check("不存在的代理 404", r.status_code == 404, r.text[:150])

    # 5. 代理列表透出绑定状态
    r = client.get("/api/proxies", headers=auth)
    check("GET /api/proxies 200", r.status_code == 200, r.text[:150])
    plist = r.json()
    by_id = {p["id"]: p for p in plist}
    check("已占用代理 bound_account_name=账号B", by_id[p1_id].get("bound_account_name") == "账号B", str(by_id[p1_id]))
    check("未使用代理 bound_account_name 为 None", by_id[p2_id].get("bound_account_name") is None, str(by_id[p2_id]))

    # 6. 用未使用代理创建成功
    payload = make_account_payload("账号E")
    payload["proxy_id"] = p2_id
    r = client.post("/api/accounts", headers=auth, json=payload)
    check("未使用代理创建 200", r.status_code == 200, r.text[:150])

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
sys.exit(1 if FAIL else 0)
