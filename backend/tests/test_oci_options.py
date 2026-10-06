"""OCI 选项查询测试：images 接口的 arch 架构过滤（mock client，不碰真实 OCI）。

运行：/tmp/ocitest/bin/python backend/tests/test_oci_options.py
（plain assert，无需 pytest；测试库用 sqlite 文件库）
"""
import os
import sys

DB_PATH = "/tmp/test_oci_options.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["MASTER_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ["MASTER_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

import app.api.oci_options as oci_options  # noqa: E402
from app.core.deps import SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token, encrypt_text, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import Account, Base, Operator  # noqa: E402

Base.metadata.create_all(bind=engine)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


def _make_account() -> int:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    db = SessionLocal()
    try:
        acc = Account(
            name="镜像测试账号",
            tenancy_ocid="ocid1.tenancy.oc1..test",
            user_ocid="ocid1.user.oc1..test",
            fingerprint="aa:bb:cc:dd",
            private_key_enc=encrypt_text(pem),
            region="ap-seoul-1",
        )
        db.add(acc)
        db.commit()
        return acc.id
    finally:
        db.close()


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    @property
    def text(self):
        return str(self._data)


UBUNTU_IMAGES = [
    {"id": "ocid1.image.arm-2204", "displayName": "Canonical-Ubuntu-22.04-aarch64-2024.01.01-0",
     "operatingSystem": "Canonical Ubuntu", "operatingSystemVersion": "22.04",
     "compartmentId": None, "timeCreated": "2024-01-01T00:00:00Z"},
    {"id": "ocid1.image.amd-2204", "displayName": "Canonical-Ubuntu-22.04-2024.01.01-0",
     "operatingSystem": "Canonical Ubuntu", "operatingSystemVersion": "22.04",
     "compartmentId": None, "timeCreated": "2024-01-01T00:00:00Z"},
    # 自定义镜像：compartmentId 非空，应被过滤掉
    {"id": "ocid1.image.custom", "displayName": "my-custom-image",
     "operatingSystem": "Canonical Ubuntu", "operatingSystemVersion": "22.04",
     "compartmentId": "ocid1.compartment.oc1..x", "timeCreated": "2024-02-01T00:00:00Z"},
]

OL_IMAGES = [
    {"id": "ocid1.image.ol9-arm", "displayName": "Oracle-Linux-9.3-aarch64-2024.01.01-0",
     "operatingSystem": "Oracle Linux", "operatingSystemVersion": "9.3",
     "compartmentId": None, "timeCreated": "2024-01-01T00:00:00Z"},
    {"id": "ocid1.image.ol9-amd", "displayName": "Oracle-Linux-9.3-2024.01.01-0",
     "operatingSystem": "Oracle Linux", "operatingSystemVersion": "9.3",
     "compartmentId": None, "timeCreated": "2024-01-01T00:00:00Z"},
]


class _FakeClient:
    async def request(self, method, service, path):
        if "Canonical%20Ubuntu" in path:
            return _FakeResp(UBUNTU_IMAGES)
        return _FakeResp(OL_IMAGES)

    async def aclose(self):
        pass


# monkeypatch：不走真实 OCI
oci_options._build_client = lambda account, region: _FakeClient()  # noqa: E731

with TestClient(app) as client:
    _db = SessionLocal()
    _db.add(Operator(username="tester", password_hash=hash_password("test123")))
    _db.commit()
    _db.close()
    client.headers.update({"Authorization": "Bearer " + create_access_token("tester")})

    account_id = _make_account()

    # 1. arch=arm：只返回 ARM 镜像
    r = client.get("/api/oci-options/images", params={"account_id": account_id, "arch": "arm"})
    data = r.json()
    check("arch=arm 200", r.status_code == 200, r.text[:120])
    check("arch=arm 只返回 ARM",
          len(data) == 2 and all(i["architecture"] == "ARM" for i in data), data)
    check("arch=arm 带版本字段",
          all(i["operating_system_version"] for i in data), data)

    # 2. arch=amd：只返回 AMD 镜像
    r = client.get("/api/oci-options/images", params={"account_id": account_id, "arch": "amd"})
    data = r.json()
    check("arch=amd 只返回 AMD",
          r.status_code == 200 and len(data) == 2
          and all(i["architecture"] == "AMD" for i in data), data)

    # 3. arch 为空：全返回，自定义镜像被过滤
    r = client.get("/api/oci-options/images", params={"account_id": account_id})
    data = r.json()
    ocids = [i["ocid"] for i in data]
    check("arch 为空返回 4 个平台镜像",
          r.status_code == 200 and len(data) == 4, data)
    check("自定义镜像被过滤", "ocid1.image.custom" not in ocids, ocids)

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
