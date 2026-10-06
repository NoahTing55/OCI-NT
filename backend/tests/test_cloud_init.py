"""cloud-init root 密码脚本 + launch_instance user_data 透传测试。

运行：/tmp/ocitest/bin/python backend/tests/test_cloud_init.py
（plain assert，无需 pytest；不碰真实 OCI）
"""
import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.expanduser("~/workspace/oci-panel/backend"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.cloud_init import build_root_password_script
from app.core.oci_client import OciClient

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------- 1. cloud-init 脚本生成 ----------
enc = build_root_password_script("P@ssw0rd!2024")
raw = base64.b64decode(enc).decode("utf-8")
check("base64 可解且为 cloud-config", raw.startswith("#cloud-config"), raw[:40])
check("含 chpasswd root 密码", "chpasswd:" in raw and "root:P@ssw0rd!2024" in raw)
check("开启 SSH 密码登录",
      "PasswordAuthentication yes" in raw and "PermitRootLogin yes" in raw)
check("runcmd 执行后清理", "runcmd:" in raw and "rm -f /tmp/oci_panel_root_access.sh" in raw)

# 特殊字符密码（含 {}: #$）用块标量原样保留，不破坏 yaml
enc2 = build_root_password_script("a{b}:c#d$e%f")
raw2 = base64.b64decode(enc2).decode("utf-8")
check("特殊字符密码原样保留", "root:a{b}:c#d$e%f" in raw2, raw2[:200])


# ---------- 2. launch_instance user_data 透传 ----------
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
client = OciClient(
    tenancy_ocid="ocid1.tenancy.oc1..t",
    user_ocid="ocid1.user.oc1..u",
    fingerprint="aa:bb:cc:dd",
    private_key_pem=pem,
    region="ap-seoul-1",
    proxy_url=None,
)
captured = {}


class _R:
    status_code = 200


async def _fake_request(method, service, path, json_body=None):
    captured.update({"method": method, "service": service, "path": path, "body": json_body})
    return _R()


client.request = _fake_request

launch_kwargs = dict(
    compartment_id="ocid1.compartment.oc1..c",
    availability_domain="AD-1",
    shape="VM.Standard.E2.1.Micro",
    ocpus=1, memory_gb=1,
    image_ocid="ocid1.image.oc1..img",
    subnet_ocid="ocid1.subnet.oc1..sub",
    display_name="test-01",
)
run(client.launch_instance(**launch_kwargs, user_data="dXNlciBkYXRh"))
check("user_data 进入 metadata",
      captured["body"].get("metadata") == {"user_data": "dXNlciBkYXRh"},
      captured["body"].get("metadata"))

captured.clear()
run(client.launch_instance(**launch_kwargs))
check("不传 user_data 时不带 metadata", "metadata" not in captured["body"],
      list(captured["body"].keys()))

print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
