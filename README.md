# OCI 多账号管理面板

自研 OCI 多账号管理 Web 面板：账号存活检查、抢机、批量开机/关机/重启、更换 IP、Cloudflare DNS 自动同步。通知只走 Telegram Bot。

## 功能

- **账号管理**：多 OCI 账号 CRUD，单 API 单代理绑定（socks5h 防 DNS 泄漏），一键存活检查（401 密钥失效 / 403 权限不足 / 超时分类）
- **抢机**：Out of host capacity 抖动重试、429 指数退避、抢到即停；成功后自动同步 Cloudflare
- **批量创建**：E5/A1/E2 等 shape 预设，三步向导（选配置 → 选账号 → 看进度），复用抢机引擎的错误分类与退避
- **实例运维**：多账号多区域聚合列表，批量开机/关机/重启/终止，编辑名称/标签
- **换 IP**：预留 IP 标准流程，换完自动同步 Cloudflare
- **Cloudflare**：Token 加密存储，A/AAAA 记录管理，一致性巡检
- **安全**：登录鉴权 + TOTP 双因素，审计日志（敏感字段脱敏），实例列表 Redis 缓存
- **系统设置**：TG、定时任务间隔、缓存等网页可配，改完即时生效

## 快速部署

```bash
git clone https://github.com/NoahTing55/OCI-NT.git /opt/oci--nt
cd /opt/oci--nt
cp .env.example .env   # 填写 MASTER_KEY 等（见下方配置说明）
docker compose up --build -d
```

只暴露 **8035** 一个端口（安全组/防火墙放行 8035 即可）：

- 面板：http://服务器IP:8035
- API 文档：http://服务器IP:8035/docs

## 配置

首次部署只需填 `.env` 里启动必需的几项：`MASTER_KEY`、`ADMIN_USERNAME` / `ADMIN_PASSWORD`（初始管理员）。其他配置（TG Bot、定时任务间隔、缓存等）在网页「系统设置」里改，保存后即时生效。

## 安全提醒

- `MASTER_KEY` 丢了所有加密数据无法解密，请备份
- 公网暴露前建议加 VPN 或 IP 白名单
- 不要提交 `.env`、私钥、Token、OCID 到仓库
