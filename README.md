# OCI 多账号管理面板

自研 OCI 多账号管理 Web 面板：账号存活检查、开机管理、批量开关机、更换 IP、Cloudflare DNS 自动同步。通知只走 Telegram Bot。

## 功能

- **总览**：账号总数、实例总数、运行中任务、今日开机统计卡片；E2/A1/E5 配额预警；最近动态
- **账号管理**：多 OCI 账号 CRUD，单 API 单代理绑定（socks5h 防 DNS 泄漏）；**导入 API** 一键建号（Config/PEM 文件选择或粘贴，自动解析填入）；一键存活检查（401 密钥失效 / 403 权限不足 / 超时分类）；账户摘要卡片（实例数、OCPU/内存、配额进度条）；自定义名称、主区域、账号成本、存活天数、开机任务、账号类型（个人免费/个人升级自动识别）、实例数、账号状态一目了然；**实例操作**列一键创建实例（自动跳转并预选账号）
- **开机管理**（原抢机任务）：4 个快捷模板（免费 AMD 1C1G / ARM 1C6G / ARM 2C12G / E5 1C6G），模板填入后 Shape、OCPU、内存、硬盘均可手动改；开机数量（1-100 台）、开机间隔（30s/60s/300s 快捷+自定义）；Out of host capacity 按间隔重试、429 指数退避；子网选填，worker 自动建网（VCN→网关→路由→子网，有则复用）；cloud-init 下发 root 密码（可填/随机/自动生成）；硬盘容量 50-16384GB 可调；开机成功默认放行所有端口；成功后 TG 推送实例信息、公网 IP、root 密码；任务可编辑（运行中/已完成除外）
- **实例运维**：多账号多区域聚合列表，批量开机/关机/重启/终止，编辑名称/标签
- **换 IP**：预留 IP 标准流程，换完自动同步 Cloudflare
- **网络**：安全组一键放行所有端口（给已开机实例补规则）
- **Cloudflare**：Token 加密存储，A/AAAA 记录管理，一致性巡检
- **安全**：登录鉴权 + TOTP 双因素，审计日志（敏感字段脱敏），实例列表 Redis 缓存
- **系统设置**：TG、定时任务间隔、缓存等网页可配，改完即时生效

## 快速部署

GitHub Actions 每次推送自动构建镜像到 GHCR，服务器直接拉取，无需本地构建：

```bash
git clone https://github.com/NoahTing55/OCI-NT.git /opt/oci--nt
cd /opt/oci--nt
cp .env.example .env   # 填写 MASTER_KEY 等（见下方配置说明）
docker compose pull
docker compose up -d
```

以后更新：`git pull && docker compose pull && docker compose up -d` 即可。启动时自动执行数据库迁移，无需手动补 SQL。

只暴露 **8035** 一个端口（安全组/防火墙放行 8035 即可）：

- 面板：http://服务器IP:8035
- API 文档：http://服务器IP:8035/docs

## 配置

首次部署只需填 `.env` 里启动必需的几项：`MASTER_KEY`、`ADMIN_USERNAME` / `ADMIN_PASSWORD`（初始管理员）。其他配置（TG Bot、定时任务间隔、缓存等）在网页「系统设置」里改，保存后即时生效。

开机成功要收到 TG 通知：在「系统设置」填 Telegram Bot Token 和 Chat ID，点"测试推送"确认。

## 安全提醒

- `MASTER_KEY` 丢了所有加密数据无法解密，请备份
- 公网暴露前建议加 VPN 或 IP 白名单
- 不要提交 `.env`、私钥、Token、OCID 到仓库
