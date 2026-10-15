#!/bin/bash
# Azure 服务主体一键创建脚本：输出面板批量导入用的 JSON
# 前提：已 az login 到目标租户（az login --tenant <TENANT_ID>）
# 用法：bash azure-sp-setup.sh "Azure-01" >> accounts.jsonl
set -e

NAME="${1:-Azure-01}"
APP_NAME="OCI-Panel-Reader"

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
SUB_NAME=$(az account show --query name -o tsv)

echo "[1/3] 创建应用注册..." >&2
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)

echo "[2/3] 创建客户端密钥..." >&2
SECRET=$(az ad app credential reset --id "$APP_ID" --query password -o tsv)

echo "[3/3] 分配 Reader 角色..." >&2
az role assignment create --assignee "$APP_ID" --role Reader \
  --scope "/subscriptions/$SUB_ID" --output none 2>/dev/null || \
  echo "  角色分配失败，请手动在 Portal 订阅 IAM 里给 $APP_NAME 配 Reader" >&2

# 输出单行 JSON（JSONL），20 个跑完后用 jq 合成数组导入面板
jq -n -c \
  --arg name "$NAME" \
  --arg tenant "$TENANT_ID" \
  --arg client "$APP_ID" \
  --arg secret "$SECRET" \
  --arg remark "$SUB_NAME" \
  '{name: $name, tenant_id: $tenant, client_id: $client, client_secret: $secret, remark: $remark}'
