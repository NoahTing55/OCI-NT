# M2 API 探针笔记（已按官方文档核对，未做真实账号调用）

> 依据：OCI Core Services / Virtual Network REST API 文档、Cloudflare API v4 文档。
> 自研 `OciClient.request(method, service, path, json_body)` 的 `path` 可直接带 query string
> （如 `/20160918/instances/{id}?action=START`），签名与请求都会正确处理。

## 一、实例操作（service = `iaas`）

| 操作 | 方法与路径 |
|---|---|
| 列表 | `GET /20160918/instances?compartmentId={tenancy_ocid}&limit=100`（可用 `availabilityDomain`、`lifecycleState` 过滤；分页靠响应头 `opc-next-page`） |
| 详情 | `GET /20160918/instances/{instanceId}` → 取 `primaryVnicId`、`lifecycleState` |
| 开机/关机/重启 | `POST /20160918/instances/{instanceId}?action=START`，action ∈ `START / STOP / SOFTSTOP / RESET / SOFTRESET` |
| 终止 | `DELETE /20160918/instances/{instanceId}`（可选 `?preserveBootVolume=true`；返回 204） |
| 编辑 | `PUT /20160918/instances/{instanceId}`，body 如 `{"displayName": "...", "freeformTags": {...}, "metadata": {...}}` |

- 开关机是异步的：返回 200 后轮询 `lifecycleState`（STARTING→RUNNING，STOPPING→STOPPED）。
- `compartmentId` 直接用 tenancy OCID（根 compartment）即可列出全部。

## 二、换 IP —— 预留 IP 流程（主方案，已核对语义）

前置：`GET /20160918/instances/{id}` → `primaryVnicId` → `GET /20160918/privateIps?vnicId={vnicId}` → 找 `isPrimary=true` 的记录 → 拿到 `privateIp` 的 OCID。

1. 查当前公网 IP：`GET /20160918/publicIps/actions/getByPrivateIpId?privateIpId={privateIpId}`（无则 404；记录 `id`/`ipAddress`/`lifetime`，用于回滚与审计）
2. 创建预留 IP（先不绑定）：`POST /20160918/publicIps`
   ```json
   {"compartmentId": "{tenancy_ocid}", "lifetime": "RESERVED", "displayName": "oci-panel-{instance名}"}
   ```
   返回新 `publicIp` 的 `id` 与 `ipAddress`。`scope` 默认可用域级；跨 AD 复用才需要 `"scope": "REGION"`。
3. 绑定到主私网 IP：`PUT /20160918/publicIps/{newPublicIpId}`
   ```json
   {"privateIpId": "{privateIp的OCID}"}
   ```
   语义（文档原文）：若该预留 IP 已绑到别的私网 IP，会先解绑再重绑；`privateIpId` 设为空字符串则解绑。
4. 验证：再次调 `getByPrivateIpId`，确认返回的新 `ipAddress` 一致。
5. 可选释放旧预留 IP：`DELETE /20160918/publicIps/{oldPublicIpId}`（旧的是临时 IP 则跳过——临时 IP 被替换后自动回收）。

⚠️ 回滚：任何一步失败，已创建未绑定的预留 IP 记得删除，避免 IP 池泄漏；操作前把旧 IP 写入审计日志。

## 三、换 IP —— 临时公网 IP（待真实账号探针）

官方没有"直接更换临时 IP"接口。待验证的假设流程：
1. `POST /20160918/publicIps`，`{"compartmentId": ..., "lifetime": "EPHEMERAL", "privateIpId": "{privateIpId}"}` —— 临时 IP 创建时必须指定 `privateIpId`；
2. 假设：同一私网 IP 只能挂一个公网 IP，新临时 IP 会自动顶掉旧的（需探针确认；若不自动，则先对旧 IP 调 `DELETE /20160918/publicIps/{oldId}` 再创建）。

## 四、Cloudflare DNS 同步（base `https://api.cloudflare.com/client/v4`，`Authorization: Bearer {token}`）

- 找 zone：`GET /zones?name={domain}` → `result[0].id`
- 找记录：`GET /zones/{zoneId}/dns_records?type=A&name={sub.domain}` → `result[0].id` 与当前 `content`
- 更新（部分字段即可）：`PATCH /zones/{zoneId}/dns_records/{recordId}`，`{"content": "{newIP}"}`
- 不存在则创建：`POST /zones/{zoneId}/dns_records`，`{"type": "A", "name": "...", "content": "...", "ttl": 1, "proxied": false}`（`ttl: 1` = 自动；`proxied` 按用户配置）
- 响应信封 `{success, errors, messages, result}`，判 `success==true`；全局限流 1200 req / 5 min，本场景绰绰有余。
- Token 最小权限：Zone → DNS → Edit。

## 五、给 M2 实现的建议顺序

1. 实例列表 + 详情（含 primaryVnicId / 主私网 IP / 当前公网 IP 三段查询链，先把这条链调通）；
2. 开关机/重启/终止（InstanceAction + 轮询状态）；
3. 编辑实例（UpdateInstance，注意 shape/OCPU 不可改，前端禁用）；
4. 预留 IP 换 IP（按 §二 流程，失败回滚 + 审计）；
5. CF 同步（换 IP 成功后自动触发 + 定时巡检比对）；
6. 临时 IP 换 IP 做最后，依赖真实账号探针结论。
