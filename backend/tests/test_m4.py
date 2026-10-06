"""M4 收尾测试：鉴权/TOTP、审计中间件、实例缓存降级。

运行：/tmp/m4test/bin/python backend/tests/test_m4.py
（plain assert，无需 pytest；测试库用 sqlite 文件库；需装 pyotp/PyJWT/qrcode/Pillow）
"""
import os
import sys

DB_PATH = "/tmp/test_m4.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///%s" % DB_PATH
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

import pyotp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password  # noqa: E402
from app.models.models import Base, OperationLog, Operator  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


# ---------- 密码哈希 / JWT 单测 ----------
h = hash_password("hello123")
check("密码哈希可校验", verify_password("hello123", h))
check("密码错误校验失败", not verify_password("wrong", h))
check("畸形哈希不抛异常", not verify_password("x", "not-a-hash"))
tok = create_access_token("admin")
check("JWT 解码出用户名", decode_access_token(tok) == "admin")
check("伪造 JWT 解码为 None", decode_access_token(tok + "x") is None)

# ---------- API 测试 ----------
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=engine)

with TestClient(app) as client:
    # --- 初始化管理员 ---
    r = client.post("/api/auth/init", json={"username": "admin", "password": "admin123"})
    check("POST /init 建首个管理员 200", r.status_code == 200, r.text[:120])
    r = client.post("/api/auth/init", json={"username": "x", "password": "y123456"})
    check("重复 init 被 403 拒绝", r.status_code == 403, r.text[:120])
    r = client.post("/api/auth/init", json={"username": "ab", "password": "123"})
    check("表非空时短密码也走 403（先查表）", r.status_code == 403, r.text[:120])

    # --- 登录 ---
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    check("密码错误 401", r.status_code == 401, r.text[:120])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    check("密码正确登录 200 且有 token", r.status_code == 200 and r.json().get("access_token"), r.text[:120])
    token = r.json()["access_token"]
    auth = {"Authorization": "Bearer " + token}

    # --- 未登录访问被拒 ---
    r = client.get("/api/accounts")
    check("无 token 访问 401", r.status_code == 401, r.text[:120])
    r = client.get("/api/accounts", headers={"Authorization": "Bearer invalid"})
    check("伪造 token 访问 401", r.status_code == 401, r.text[:120])
    r = client.get("/api/accounts", headers=auth)
    check("带 token 访问 200", r.status_code == 200, r.text[:120])

    # --- /api/ping 公开 ---
    r = client.get("/api/ping")
    check("GET /api/ping 无需登录", r.status_code == 200, r.text[:120])

    # --- me / 改密码 ---
    r = client.get("/api/auth/me", headers=auth)
    check("GET /me 返回用户名", r.status_code == 200 and r.json()["username"] == "admin", r.text[:120])
    r = client.post("/api/auth/change-password", headers=auth,
                    json={"old_password": "admin123", "new_password": "newpass456"})
    check("改密码 200", r.status_code == 200, r.text[:120])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "newpass456"})
    check("新密码可登录", r.status_code == 200 and r.json().get("access_token"), r.text[:120])
    token = r.json()["access_token"]
    auth = {"Authorization": "Bearer " + token}

    # --- TOTP 绑定/启用 ---
    r = client.post("/api/auth/totp/setup", headers=auth)
    check("totp setup 返回二维码与密钥", r.status_code == 200 and r.json().get("qr_data_uri", "").startswith("data:image/png;base64,") and r.json().get("secret"), r.text[:120])
    secret = r.json()["secret"]
    r = client.post("/api/auth/totp/enable", headers=auth, json={"totp_code": "000000"})
    check("错误动态码启用失败 400", r.status_code == 400, r.text[:120])
    good_code = pyotp.TOTP(secret).now()
    r = client.post("/api/auth/totp/enable", headers=auth, json={"totp_code": good_code})
    check("正确动态码启用成功", r.status_code == 200, r.text[:120])

    # --- TOTP 登录流程 ---
    r = client.post("/api/auth/login", json={"username": "admin", "password": "newpass456"})
    check("TOTP 用户无动态码返回 totp_required", r.status_code == 401 and r.json().get("detail") == "totp_required", r.text[:120])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "newpass456", "totp_code": "000000"})
    check("TOTP 用户错误动态码 401", r.status_code == 401, r.text[:120])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "newpass456",
                                             "totp_code": pyotp.TOTP(secret).now()})
    check("TOTP 用户正确动态码登录 200", r.status_code == 200 and r.json().get("access_token"), r.text[:120])
    token2 = r.json()["access_token"]
    auth2 = {"Authorization": "Bearer " + token2}

    # --- TOTP 解绑 ---
    r = client.post("/api/auth/totp/disable", headers=auth2, json={"password": "wrong"})
    check("解绑密码错误 401", r.status_code == 401, r.text[:120])
    r = client.post("/api/auth/totp/disable", headers=auth2, json={"password": "newpass456"})
    check("解绑成功", r.status_code == 200, r.text[:120])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "newpass456"})
    check("解绑后无需动态码可登录", r.status_code == 200, r.text[:120])

    # --- 审计中间件：写操作自动记录 + 脱敏 ---
    db = SessionLocal()
    before = db.query(OperationLog).count()
    db.close()
    r = client.post("/api/proxies", headers=auth,
                    json={"name": "测试代理", "scheme": "http", "host": "10.0.0.1",
                          "port": 8080, "username": "u", "password": "s3cr3t-pw"})
    check("新建代理 200", r.status_code == 200, r.text[:120])
    proxy_id = r.json()["id"]
    db = SessionLocal()
    logs = db.query(OperationLog).order_by(OperationLog.id.desc()).limit(3).all()
    db.close()
    hit = [l for l in logs if l.action == "proxy.create"]
    check("中间件自动记 proxy.create", len(hit) == 1, [l.action for l in logs])
    d = hit[0].detail if hit else ""
    check("审计摘要脱敏 password", "***" in d and "s3cr3t-pw" not in d, d[:200])
    check("审计记录操作人 admin", hit and hit[0].operator == "admin", hit[0].operator if hit else "")
    r = client.delete(f"/api/proxies/{proxy_id}", headers=auth)
    check("删除代理 200", r.status_code == 200, r.text[:120])

    # --- 登录接口不记审计（含密码，完全跳过） ---
    db = SessionLocal()
    n_login = db.query(OperationLog).filter(OperationLog.action.like("auth.login%")).count()
    n_after = db.query(OperationLog).count()
    db.close()
    check("登录请求不产生审计记录", n_login == 0, n_login)
    check("写操作产生了审计增量", n_after > before, (before, n_after))

    # --- 匿名写操作记 anonymous（无 token 的 POST 被 401，但中间件仍记录） ---
    r = client.post("/api/proxies", json={"name": "x", "scheme": "http", "host": "1.1.1.1", "port": 1})
    db = SessionLocal()
    anon = db.query(OperationLog).filter(OperationLog.operator == "anonymous").count()
    db.close()
    check("401 的写请求仍审计且操作人为 anonymous", r.status_code == 401 and anon >= 1, (r.status_code, anon))

    # --- 实例缓存降级：无 Redis 时直接查询不报错 ---
    r = client.get("/api/instances", headers=auth)
    check("无 Redis 时实例列表降级直查 200", r.status_code == 200 and "items" in r.json(), r.text[:120])
    from app.services.instances import invalidate_instance_cache  # noqa: E402
    try:
        invalidate_instance_cache()
        check("无 Redis 时缓存失效静默跳过", True)
    except Exception as e:
        check("无 Redis 时缓存失效静默跳过", False, str(e)[:100])

print()
print(f"共 {len(PASS)} 通过，{len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
    raise SystemExit(1)
