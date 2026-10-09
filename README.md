# OCI 多账号管理面板

自研 OCI 多账号管理 Web 面板：账号存活检查、开机管理、批量开关机、更换 IP、Cloudflare DNS 自动同步。通知只走 Telegram Bot。

## 功能

- **总览**：账号/实例/任务统计，配额预警
- **账号管理**：**导入 API** 建号（Config/PEM 文件或粘贴，自动解析）；存活检查；账号类型自动识别；一键创建实例
- **开机管理**：4 个模板（免费 AMD 1C1G / ARM 1C6G / ARM 2C12G / E5 1C6G），配置可改；自动建网；cloud-init root 密码；硬盘可调；成功 TG 推送实例信息、公网 IP、密码；任务可编辑
- **实例运维**：多账号多区域聚合列表，批量开机/关机/重启/终止，编辑名称/标签
- **换 IP**：预留 IP 标准流程，换完自动同步 Cloudflare
- **网络**：安全组一键放行所有端口（给已开机实例补规则）
- **Cloudflare**：Token 加密存储，A/AAAA 记录管理，一致性巡检
- **安全**：登录鉴权 + TOTP 双因素，审计日志（敏感字段脱敏），实例列表 Redis 缓存
- **系统设置**：TG、定时任务间隔、缓存等网页可配，改完即时生效

## 快速部署

### 前置要求

- 服务器安装 Docker 和 Docker Compose（`docker compose version` 能输出版本即可）
- 安全组/防火墙放行 **8035** 端口

### 首次部署

GitHub Actions 每次推送自动构建镜像到 GHCR，服务器直接拉取，无需本地构建：

```bash
# 1. 拉代码
git clone https://github.com/NoahTing55/OCI-NT.git /opt/oci--nt
cd /opt/oci--nt

# 2. 配环境变量
cp .env.example .env
```

编辑 `.env`，必填三项：

| 变量 | 说明 |
|---|---|
| `MASTER_KEY` | 加密密钥，32 字节 hex，自己生成：`openssl rand -hex 32`，**丢了所有加密数据无法解密** |
| `ADMIN_USERNAME` | 初始管理员用户名 |
| `ADMIN_PASSWORD` | 初始管理员密码 |

```bash
# 3. 拉镜像并启动
docker compose pull
docker compose up -d

# 4. 看状态（api/worker/postgres/redis 都 Up 才算好）
docker compose ps

# 5. 看日志排错
docker compose logs --tail=50 api
docker compose logs --tail=50 worker
```

启动时自动执行数据库迁移，无需手动补 SQL。

打开面板：http://服务器IP:8035（API 文档：http://服务器IP:8035/docs），用上面配的管理员账号登录。

### 以后更新

```bash
cd /opt/oci--nt
git pull
docker compose pull
docker compose up -d
```

注意 `up` 不要加 `--build`，否则会走本地构建而不是拉预构建镜像。

### GHCR 拉取要登录？

镜像包是公开的，一般不用登录。如果 `docker compose pull` 报 `unauthorized`：

```bash
# GitHub → Settings → Developer settings → Personal access tokens → 建 classic token，勾 read:packages
echo "你的token" | docker login ghcr.io -u 你的GitHub用户名 --password-stdin
```

### 常见问题

- **api 起不来**：`docker compose logs api` 看报错；常见是 `.env` 里 `MASTER_KEY` 格式不对
- **前端空白/接口 404**：确认只暴露了 8035，且 `docker compose ps` 里 api 是 healthy
- **定时任务没跑**：`docker compose logs worker` 确认 celery worker 正常

## 配置

首次部署只需填 `.env` 里启动必需的几项：`MASTER_KEY`、`ADMIN_USERNAME` / `ADMIN_PASSWORD`（初始管理员）。其他配置（TG Bot、定时任务间隔、缓存等）在网页「系统设置」里改，保存后即时生效。

开机成功要收到 TG 通知：在「系统设置」填 Telegram Bot Token 和 Chat ID，点"测试推送"确认。

## 安全提醒

- `MASTER_KEY` 丢了所有加密数据无法解密，请备份
- 公网暴露前建议加 VPN 或 IP 白名单
- 不要提交 `.env`、私钥、Token、OCID 到仓库
