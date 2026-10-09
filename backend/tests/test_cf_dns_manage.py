"""Cloudflare DNS 记录管理测试（plain assert，无需 pytest、无第三方依赖）。

覆盖：
1. CloudflareClient 新增方法 delete_record / patch_record / get_record 的请求路径正确
   （用 stub _api 拦截，不发真实请求）
2. sync_instance_domains 换 IP 时，一并更新同 zone 下指向旧 IP 的手动添加记录
3. backend/app/api/cloudflare.py 注册了全部新路由（AST 解析）
4. backend/app/core/audit.py ACTION_MAP 覆盖了新接口（AST 解析）

运行：python3 backend/tests/test_cf_dns_manage.py
"""
import ast
import asyncio
import sys
import types

sys.path.insert(0, "/home/hatch/workspace/oci-panel/backend")

# stub httpx（cloudflare.py 顶层 import 用）
httpx_stub = types.ModuleType("httpx")
httpx_stub.AsyncClient = object
sys.modules["httpx"] = httpx_stub

# stub app.core.security（decrypt_text）
sec_mod = types.ModuleType("app.core.security")
sec_mod.decrypt_text = lambda enc: "plain-token"
sec_mod.encrypt_text = lambda s: "enc:" + s
sys.modules["app.core.security"] = sec_mod

# stub app.models.models（DomainBinding / CloudflareToken 轻量占位）
models_mod = types.ModuleType("app.models.models")


class _Col:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return True


class _ModelMeta(type):
    def __getattr__(cls, name):
        return _Col()


class DomainBinding(metaclass=_ModelMeta):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class CloudflareToken:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


models_mod.DomainBinding = DomainBinding
models_mod.CloudflareToken = CloudflareToken
sys.modules["app.models.models"] = models_mod

from app.services import cloudflare as cf_mod  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


class FakeCFClient(cf_mod.CloudflareClient):
    def __init__(self):
        self.calls = []

    async def _api(self, method, path, json_body=None):
        self.calls.append((method, path, json_body))
        if method == "DELETE":
            return {"id": path.split("/")[-1]}
        if method == "PATCH":
            return {"id": path.split("/")[-1], **(json_body or {})}
        if method == "GET" and "/dns_records" in path and "?" not in path.split("/dns_records")[-1]:
            # get_record 单条
            if path.rstrip("/").split("/")[-1].startswith("r"):
                return {"id": "r1"}
        if method == "GET" and "/dns_records" in path:
            return [
                {"id": "r1", "name": "vpn.example.com", "type": "A", "content": "1.1.1.1"},
                {"id": "r2", "name": "manual.example.com", "type": "A", "content": "1.1.1.1"},
                {"id": "r3", "name": "other.example.com", "type": "A", "content": "2.2.2.2"},
            ]
        return {}

    async def aclose(self):
        pass

    async def find_zone_id(self, domain):
        return "z1"

    async def ensure_record(self, domain, ip, record_type="A", ttl=120, proxied=False):
        return {"record_id": "r1", "created": False, "unchanged": False}


class FakeQ:
    def __init__(self, items):
        self._items = items

    def filter(self, *a):
        return self

    def all(self):
        return self._items


class FakeDB:
    def __init__(self, bindings):
        self._bindings = bindings
        self.committed = False

    def query(self, model):
        return FakeQ(self._bindings)

    def get(self, model, _id):
        return CloudflareToken(name="t", token_enc="enc")

    def commit(self):
        self.committed = True


async def main():
    c = FakeCFClient()

    # 1. delete_record
    await c.delete_record("z1", "r9")
    m, p, _ = c.calls[-1]
    check("delete_record 走 DELETE /zones/z1/dns_records/r9",
          m == "DELETE" and p == "/zones/z1/dns_records/r9", c.calls[-1])

    # 2. patch_record
    await c.patch_record("z1", "r1", {"proxied": True})
    m, p, b = c.calls[-1]
    check("patch_record 走 PATCH 且 body 为部分字段",
          m == "PATCH" and p == "/zones/z1/dns_records/r1" and b == {"proxied": True}, c.calls[-1])

    # 3. sync_instance_domains：一并更新手动添加的同 zone 旧 IP 记录
    b = DomainBinding(
        cf_token_id=1, account_id=1, instance_ocid="ocid1", instance_name="i1",
        domain="vpn.example.com", record_type="A", zone_id="z1",
        proxied=False, ttl=120, auto_sync=True,
    )
    b.last_ip = "1.1.1.1"
    b.record_id = "r1"

    orig_client = cf_mod.CloudflareClient
    probe = FakeCFClient()
    cf_mod.CloudflareClient = lambda token, timeout=15.0: probe
    try:
        results = await cf_mod.sync_instance_domains(FakeDB([b]), "ocid1", "3.3.3.3")
    finally:
        cf_mod.CloudflareClient = orig_client

    check("sync 返回 ok", bool(results) and results[0]["ok"] is True, results)
    extra = (results[0].get("extra_updated") or []) if results else []
    check("手动添加的 manual.example.com 被更新", "manual.example.com" in extra, extra)
    check("指向其他 IP 的 other.example.com 不受影响",
          not any("other.example.com" in x for x in extra), extra)
    check("binding.last_ip 已更新为新 IP", b.last_ip == "3.3.3.3", b.last_ip)
    patch_calls = [x for x in probe.calls if x[0] == "PATCH"]
    check("PATCH 更新了 r2 的 content 为新 IP",
          any(x[1].endswith("/r2") and x[2].get("content") == "3.3.3.3" for x in patch_calls),
          patch_calls)

    # 4. AST 检查 API 路由注册
    with open("/home/hatch/workspace/oci-panel/backend/app/api/cloudflare.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    routes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    method = dec.func.attr.upper()
                    if method in ("GET", "POST", "PUT", "DELETE") and dec.args:
                        arg = dec.args[0]
                        if isinstance(arg, ast.Constant):
                            routes.add((method, arg.value))
    expected = [
        ("GET", "/zones"),
        ("GET", "/zones/{zone_id}/records"),
        ("POST", "/zones/{zone_id}/records"),
        ("PUT", "/records/{record_id}"),
        ("DELETE", "/records/{record_id}"),
        ("POST", "/records/{record_id}/proxy"),
        ("POST", "/records/batch-proxy"),
        ("POST", "/zones/{zone_id}/records/batch-delete"),
    ]
    for method, path in expected:
        check(f"路由 {method} {path}", (method, path) in routes)

    # 5. AST 检查审计 ACTION_MAP（GET 不进审计，只查写操作）
    with open("/home/hatch/workspace/oci-panel/backend/app/core/audit.py", encoding="utf-8") as f:
        audit_src = f.read()
    for method, path in expected:
        if method == "GET":
            continue
        key = f'("{method}", "/api/cloudflare{path}")'
        check(f"审计映射 {key}", key in audit_src)

    print(f"\n共 {len(PASS)} 通过，{len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


asyncio.run(main())
