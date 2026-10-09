# OCI 新区域刷新步骤

当 Oracle 上线新区域时，按以下步骤把新区域补进面板。预计耗时 15-20 分钟。

## 1. 核对官方区域列表

打开 Oracle 官方区域文档，复制最新的区域标识符列表：
https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm

只看商业云（OC1）区域，政府云（OC2/OC3/OC4/OC10）不用管。

## 2. 对比现有列表

打开 `backend/app/api/region_subscription.py`，找到 `FALLBACK_OCI_REGIONS`，
把官方列表逐个对比，找出缺失的区域标识符（如 `ap-xxx-1`）。

同时检查 `frontend/src/views/Accounts.vue` 和 `frontend/src/views/Sniper.vue`
中的 `REGION_CN` 映射，确认缺失区域是否有中文名。

## 3. 补录区域

### 3.1 后端（1 处）

在 `backend/app/api/region_subscription.py` 的 `FALLBACK_OCI_REGIONS` 中按字母顺序
加入新区标识符，保持每行 4 个的排版。

### 3.2 前端（2 处）

在以下两个文件的 `REGION_CN` 映射中加入 `"新区标识": "中文名"`：
- `frontend/src/views/Accounts.vue`
- `frontend/src/views/Sniper.vue`

中文名参考 Oracle 官方中文文档的区域名称翻译。

### 3.3 显示规则（不要改）

- 账号表格"主区域"列：只显示中文名（如"凤凰城"）
- 订阅区域下拉：显示"中文名 + 区域标识"（如"凤凰城 us-phoenix-1"）

## 4. 验证

```bash
# 后端语法检查
python3 -m py_compile backend/app/api/region_subscription.py

# 前端构建
cd frontend && npm run build

# 相关测试（如有）
python3 -m pytest backend/tests/test_region_subscription.py -v
```

## 5. 推送

推送到 GitHub main 分支，等待 Actions 构建成功后通知用户部署：

```bash
cd /opt/oci--nt
docker compose pull && docker compose up -d
```

## 附：2026 年已核对记录

- 2026-10-09：核对官方文档，45 个商业区域已全部在列，无遗漏。
  - 马来西亚西2 `ap-kulai-2`（2026-02 上线）：已在列，中文名"古来"
  - 新加坡西 `ap-singapore-2`：已在列，中文名"新加坡西"
