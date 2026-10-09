"""区域订阅接口路径/请求体回归测试。

背景：2026-10-07 升级号 oci-smtqya 查区域订阅返回 404，
原因查明——regionSubscriptions 是 Identity 20180419 API 的资源，
之前代码误调了 20160918 路径（该版本无此资源，与权限无关）；
且创建订阅 body 字段应为 regionKey（IAD/PHX/FRA），不是 regionName。

本测试用假 request 层断言：
- 查询/订阅都走 /20180419/regionSubscriptions
- 创建 body 带 regionKey，并能把 us-ashburn-1 解析成 IAD

运行：python backend/tests/test_region_subscription_api.py（plain assert，无需 pytest）
"""
import asyncio
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/oci-panel/backend")

from app.core.oci_client import OciClient  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient(OciClient):
    """绕过 __init__（不建 httpx 客户端），只记录 request 调用。"""

    def __init__(self):
        self.calls = []
        self.regions_payload = [
            {"regionName": "us-ashburn-1", "regionKey": "IAD"},
            {"regionName": "us-phoenix-1", "regionKey": "PHX"},
        ]
        self.fail_regions = False

    async def request(self, method, service, path, json_body=None):
        self.calls.append((method, service, path, json_body))
        if path == "/20160918/regions":
            if self.fail_regions:
                return FakeResp(500, {})
            return FakeResp(200, self.regions_payload)
        if path.startswith("/20180419/regionSubscriptions"):
            if method == "GET":
                return FakeResp(200, [
                    {"regionName": "us-phoenix-1", "regionKey": "PHX",
                     "status": "READY", "isHomeRegion": True},
                ])
            return FakeResp(201, {})
        # 任何 20160918/regionSubscriptions 调用都是回归 bug
        return FakeResp(404, {"code": "NotAuthorizedOrNotFound"})

    async def aclose(self):
        pass


async def main():
    tenancy = "ocid1.tenancy.oc1..test"

    # ---------- 1. 查询走 20180419 ----------
    c = FakeClient()
    subs = await c.list_region_subscriptions(tenancy)
    check("查询走 /20180419/regionSubscriptions",
          c.calls[0][2] == f"/20180419/regionSubscriptions?tenancyId={tenancy}",
          c.calls[0][2])
    check("查询结果字段正确",
          subs[0]["regionName"] == "us-phoenix-1" and subs[0]["isHomeRegion"] is True)

    # ---------- 2. 旧路径不再被使用 ----------
    check("没有任何调用命中 /20160918/regionSubscriptions",
          not any("regionSubscriptions" in call[2] and "20160918" in call[2]
                  for call in c.calls))

    # ---------- 3. 创建：regionName 解析为 regionKey，body 字段正确 ----------
    c2 = FakeClient()
    await c2.create_region_subscription(tenancy, "us-ashburn-1")
    method, service, path, body = c2.calls[-1]
    check("创建走 POST /20180419/regionSubscriptions",
          method == "POST" and path == "/20180419/regionSubscriptions", (method, path))
    check("body 用 regionKey=IAD 而非 regionName",
          body == {"tenancyId": tenancy, "regionKey": "IAD"}, body)
    check("resolve_region_key 用实时列表解析",
          await c2.resolve_region_key("us-phoenix-1") == "PHX")

    # ---------- 4. regions 接口失败时用内置映射兜底 ----------
    c3 = FakeClient()
    c3.fail_regions = True
    check("regions 失败时内置映射兜底 IAD",
          await c3.resolve_region_key("us-ashburn-1") == "IAD")

    # ---------- 5. 404 仍然抛出可识别错误（供上层兜底为空列表） ----------
    class NotFoundClient(FakeClient):
        async def request(self, method, service, path, json_body=None):
            self.calls.append((method, service, path, json_body))
            return FakeResp(404, {"code": "NotAuthorizedOrNotFound"})

    c4 = NotFoundClient()
    try:
        await c4.list_region_subscriptions(tenancy)
        check("查询 404 抛 RuntimeError", False)
    except RuntimeError as e:
        check("查询 404 抛 RuntimeError 且含 404", "404" in str(e), str(e)[:80])


asyncio.run(main())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
