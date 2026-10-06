# OCI 多账号管理面板（自研，方案B）

通过 OCI API 管理多个甲骨文云账号：账户存活检查、抢机、批量开机、更换 IP、编辑实例、单API单代理、Cloudflare DNS 自动同步。通知渠道只用 Telegram Bot。

## M1 已实现（本骨架）

- 后端骨架：FastAPI + SQLAlchemy + PostgreSQL + Redis + Celery（依赖已 pin）
- 自研 OCI 调用层：httpx 异步 + OCI Signature V1 手动签名（不用 oci-sdk），每账号独立 client、独立代理绑定
- 数据模型：Account / Proxy / SnipeTask / BatchTask / DomainBinding / OperationLog
- API：账号 CRUD、代理 CRUD、账号-代理一对一绑定、一键存活检查（200正常/401密钥失效/403权限不足/超时网络异常 分类返回）
- 定时全量存活检查（APScheduler，间隔可配置），状态变更打日志 + 预留 TG 推送调用点
- Telegram Bot 通知模块（`core/telegram.py`，`send_message(text)`）
- 前端：侧边导航布局 + 账号管理页 + 代理管理页（Vue 3 + Element Plus，可调通后端 API）

## M2 已实现

- **实例运维**：`GET /api/instances` 多账号并发聚合查询（含公网/私网 IP），支持按账号/区域/状态筛选；单个账号失败不影响其他（记入 errors）
- **编辑实例**：`PUT /api/instances/{account_id}/{instance_id}`，只允许 display_name / freeform_tags / metadata；shape、OCPU、内存直接拒绝并返回明确中文错误
- **批量操作**：`POST /api/batch` 创建任务 → Celery worker 并发执行（开机 START / 关机 STOP / 重启 RESET / 终止 DELETE），`GET /api/batch/{id}` 轮询进度（成功/失败数 + 明细）；终止需 `confirm=true` 二次确认；完成后 TG 推送汇总
- **Celery worker 独立服务**：docker-compose 新增 `worker` 服务（`celery -A app.workers.celery_app.celery worker`）；APScheduler 定时检查保持在 api 进程内不动
- **换 IP**：`POST /api/network/change-ip` 预留 IP 标准流程（查主 VNIC/主私网 IP → 操作前记旧 IP 审计 → 创建预留 IP → 绑定到主私网 IP（自动顶掉旧绑定）→ 可选释放旧预留 IP → 回查确认 → 自动触发 CF 同步 → TG 推送）；临时 IP 走 `POST /api/network/ephemeral-ip/probe`（未实现，返回探针计划，待真实账号验证）
- **Cloudflare**：Token 加密存储（权限最小化：Zone.DNS 编辑）；域名绑定 CRUD（新建时先解析 zone_id，权限不足直接暴露）；手动同步 / 一键同步全部 / 一致性巡检（比对 CF 记录与 last_ip）；换 IP 成功后自动同步该实例的 auto_sync 域名；支持小黄云开关与 TTL
- **代理测速**：定时任务（`PROXY_SPEEDTEST_MINUTES`，默认 30 分钟）经代理请求轻量探针测延迟，更新 Proxy 状态/延迟；失效只 TG 告警，不自动换绑
- **Alembic**：`backend/alembic.ini` + 幂等迁移 `0001_m2`（建缺失表/补 `accounts.compartment_ocid` / `domain_bindings.cf_token_id,ttl`），与 lifespan `create_all` 兼容；已有库执行 `cd backend && alembic upgrade head`
- **账号模型**：新增 `compartment_ocid` 字段（实例/网络查询用，为空则用 tenancy OCID）
- **前端**：实例运维页（聚合列表、多选、批量操作、进度弹窗、编辑、换 IP）、网络页（换 IP 操作、CF Token 管理、域名绑定管理、手动同步、一致性巡检、一键同步）

## M3 已实现

- **抢机引擎**：`workers/sniper.py` 常驻 api 进程，每个任务独立 asyncio worker；`classify_launch_error` 按错误文本分类（Out of host capacity/500 抖动重试、429 指数退避、400/401 停任务告警）；`AccountRateLimiter` 单账号限流（12次/分钟，429 自动降频）；抢到即停（CAS + 创建前查存量）；成功后 TG 推送 + 等公网 IP + 自动 CF 同步；`SnipeLog` 任务日志保留 7 天
- **抢机 API**：`POST /api/sniper`（同一账号同一 shape 只允许一个进行中任务）、启动/暂停（CAS）、日志分页、内置场景模板（ARM 4C24G × 4 区域 + AMD 免费机）
- **前端**：抢机任务页（任务列表/新建表单/模板一键填入/日志抽屉 3 秒轮询）

## M4 已实现（批量创建实例）

- **两步式流程**：第一步选配置（shape 预设下拉含 VM.Standard.E5.Flex、OCPU/内存、每账号台数、命名前缀、重试模式 direct/retry、默认镜像/子网/可用域、保存/载入模板）；第二步选账号（多选，每行可覆盖 region/镜像/子网/可用域，"应用到全部"快捷）
- **引擎**：`workers/batch_create.py` 常驻 api 进程，复用抢机引擎的 `classify_launch_error` 与退避策略；每个 task 一个 worker，task 内信号量 4 并发；direct 模式单次尝试，retry 模式按错误分类重试（安全阀 1000 次）；支持取消（未完成 item 置 cancelled）；实例名 = 前缀+序号（如 e5-01）；成功后 TG 汇总推送
- **API**：`POST /api/batch-create`（建任务即自动启动）、`GET /{id}`（任务+items 进度轮询）、`POST /{id}/cancel`、`DELETE /{id}`、模板 CRUD（`/templates`）、shape 预设（`/shape-presets`）；写操作记审计日志；不硬编码任何 shape 的 OCPU/内存上下限
- **前端**：批量创建页（三步向导：选配置 → 选账号 → 进度，3 秒轮询）
- **迁移**：`alembic/versions/0003_batch_create.py`（3 张表，幂等）

## TODO 待实现

- 临时公网 IP 更换待真实账号做 API 探针验证（`POST /api/network/ephemeral-ip/probe` 已返回探针计划）

## 系统设置（Web 化）

大部分配置不用再 SSH 改 `.env`，在网页「系统设置」里改，保存后即时生效（定时任务间隔会自动重排，无需重启）。

**网页可配（`GET/PUT /api/settings`，需登录）：**

| 分组 | 设置项 | 说明 |
|---|---|---|
| Telegram 通知 | `TG_BOT_TOKEN`（加密存储） | Bot Token，留空回退环境变量 |
| Telegram 通知 | `TG_CHAT_ID` | 接收通知的聊天 ID |
| 定时任务 | `CHECK_INTERVAL_MINUTES` | 存活检查间隔（分钟），默认 360 |
| 定时任务 | `PROXY_SPEEDTEST_MINUTES` | 代理测速间隔（分钟），默认 30 |
| 定时任务 | `SNIPE_LOG_RETENTION_DAYS` | 抢机日志保留天数，默认 7 |
| 缓存 | `INSTANCE_CACHE_TTL` | 实例列表 Redis 缓存秒数，0 关闭，默认 60 |
| 登录安全 | `JWT_EXPIRE_MINUTES` | JWT 有效期（分钟），默认 720，只影响新签发的 Token |

读取优先级：**DB（网页设置）> 环境变量 > 代码默认值**。`TG_BOT_TOKEN` 入库前 Fernet 加密，API 永不返回明文；`POST /api/settings/test-telegram` 可发一条测试消息验证。

**必须保留 `.env`（启动前就需要，DB 不可用）：** `MASTER_KEY`（加密主密钥，丢了所有加密数据无法解密）、`DATABASE_URL` / `REDIS_URL`（数据库连接）、`JWT_SECRET_KEY`（无则回退 `MASTER_KEY`）、`ADMIN_USERNAME` / `ADMIN_PASSWORD`（仅首次初始化管理员用）。

## M4 已实现（收尾加固）

- **登录鉴权**：`POST /api/auth/login`（JWT，12 小时有效期）；`POST /api/auth/init` 初始化首个管理员（表非空自动关闭）；首次启动也可用 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 环境变量自动创建；除 `/api/auth/*` 与 `/api/ping` 外全部接口要求 Bearer JWT；前端登录页（TOTP 用户两步：先密码，返回 `totp_required` 再输动态码）、路由守卫、401 自动回登录页
- **TOTP 双因素**：`core/totp.py`（pyotp），安全设置页扫码绑定 → 输入动态码启用 → 登录时校验；密钥 Fernet 加密入库；解绑需密码二次确认
- **审计日志中间件**：`core/audit.py` 的 `AuditMiddleware` 自动记录所有 POST/PUT/DELETE/PATCH（操作人从 JWT 取，无则记 anonymous；`/api/auth/login` 完全跳过、`/api/auth/*` 不记参数摘要；`private_key`/`password`/`token`/`secret`/`totp` 类字段脱敏为 `***`）；路由层手动 `log_operation` 已全部移除（统一走中间件，action 命名不变）；后台 worker（批量任务完成、抢机成功、批量创建完成）仍手动记录
- **实例列表 Redis 缓存**：`services/instances.py` 的 `fetch_all_instances_cached`（key 含账号+筛选条件，TTL 默认 60 秒可配）；批量操作/换 IP/编辑/抢机成功/批量创建完成后自动失效；Redis 不可用时降级直查
- **迁移**：`alembic/versions/0004_m4.py`（operators 表，幂等）
- **前端**：登录页、退出按钮、安全设置页（TOTP 绑定/解绑、改密码）

## 数据迁移（Alembic）

lifespan 里 `Base.metadata.create_all` 保留（新库一键建表，幂等）；已有库的结构变更走 Alembic：

```bash
cd backend
alembic upgrade head   # 0001_m2 是幂等的：只建缺失的表、只加缺失的列，不破坏已有数据
```

## Docker 一键运行（单端口部署）

```bash
cd oci-panel
cp .env.example .env
# 编辑 .env，至少填写 MASTER_KEY（生成方法见 .env.example 注释）
# 如需网页改不动的配置（MASTER_KEY/数据库/初始管理员），也在 .env 里填好
docker compose up --build -d
```

只暴露 **8035** 一个端口（VPS 安全组/防火墙只需放行 8035）：

- 面板：http://服务器IP:8035（本地就是 http://localhost:8035）
- 后端 API 文档：http://服务器IP:8035/docs

说明：api 镜像是多阶段构建（`backend/Dockerfile`）——第一阶段用 node:20 打包前端（`npm run build`），第二阶段 Python 运行 FastAPI 并直接托管 `dist` 产物；`/` 打开面板、`/api/*` 走接口、`/docs` 看文档，同源无跨域。前端不再独立跑 dev server。

## 本地开发运行（前后端分离，仅开发调试用）

```bash
# 1. 起 postgres 和 redis
docker compose up -d postgres redis

# 2. 后端（需 Python 3.11）
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # 按需把 host 改成 localhost
uvicorn app.main:app --reload --port 8000
# 注：本地直接跑后端时没有前端构建产物（/app/static 不存在），
# main.py 会告警并仅提供 API 服务，前端用下面的 dev server 访问

# 3. 前端（需 Node 20）
cd frontend
npm install
npm run dev   # http://localhost:5173，/api 通过 vite 代理转到后端
```

## 目录结构

```
oci-panel/
├── docker-compose.yml      # api / postgres / redis / worker(Celery)，只暴露 8035
├── .env.example            # 环境变量模板（含 MASTER_KEY 等占位符）
├── backend/
│   ├── requirements.txt    # 依赖（版本已 pin，含 alembic）
│   ├── Dockerfile          # 多阶段构建：node 打包前端 → Python 运行并托管 dist
│   ├── alembic.ini         # 数据迁移配置
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/0001_m2.py  # 幂等迁移：补表/补列
│   └── app/
│       ├── main.py         # FastAPI 入口（lifespan：建表+启动定时任务）
│       ├── core/
│       │   ├── config.py   # .env 配置
│       │   ├── security.py # Fernet 加密（私钥/代理密码/CF Token）
│       │   ├── oci_client.py # 自研 OCI 签名客户端（单API单代理核心）
│       │   ├── oci_factory.py # 按账号构建绑定代理的 client
│       │   ├── telegram.py # TG Bot 推送（通知只保留 TG）
│       │   ├── deps.py     # DB 会话依赖
│       │   ├── audit.py    # 审计日志中间件（自动记写操作，敏感字段脱敏）
│       │   ├── totp.py     # TOTP 双因素（pyotp + 二维码）
│       │   └── redis_client.py # Redis 客户端（缓存，故障自动降级）
│       ├── models/models.py  # Account(+compartment_ocid)/Proxy/BatchTask/DomainBinding/CloudflareToken/OperationLog
│       ├── schemas/schemas.py
│       ├── api/
│       │   ├── accounts.py   # 账号 CRUD + 代理一对一绑定
│       │   ├── proxies.py    # 代理 CRUD
│       │   ├── health.py     # 存活检查
│       │   ├── instances.py  # 实例聚合列表 + 编辑实例
│       │   ├── batch.py      # 批量任务（创建/列表/进度查询）
│       │   ├── network.py    # 换 IP（预留 IP 流程）+ 临时 IP 探针
│       │   └── cloudflare.py # CF Token/域名绑定/同步/巡检
│       ├── services/
│       │   ├── liveness.py    # 存活检查 + 状态分类 + 变更通知
│       │   ├── instances.py   # 实例聚合查询（含 IP 补全）
│       │   ├── proxy_check.py # 代理测速
│       │   └── cloudflare.py  # CF DNS 同步（ensure_record/自动同步）
│       └── workers/
│           ├── scheduler.py   # APScheduler：存活检查 + 代理测速
│           ├── celery_app.py  # Celery 应用（Redis broker）
│           ├── batch_tasks.py # 批量任务执行器
│           └── sniper.py      # 抢机引擎（M3 TODO 占位，策略写在注释）
└── frontend/               # Vue 3 + Element Plus
    └── src/
        ├── views/Accounts.vue  # 账号管理页
        ├── views/Proxies.vue   # 代理管理页
        ├── views/Instances.vue # 实例运维页（聚合/批量/编辑/换IP）
        └── views/Network.vue   # 网络页（换IP/CF Token/域名绑定/巡检）
```

## 安全提醒

- `MASTER_KEY` 丢失后所有加密数据无法解密，请备份到安全的地方
- 面板默认只适合跑在内网/受信网络，公网暴露前请加 VPN 或 IP 白名单
- 仓库中不要提交真实 `.env`、私钥、Token、OCID
