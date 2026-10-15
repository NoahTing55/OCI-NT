<template>
  <div>
    <el-button type="primary" @click="openCreate">新建 Azure 账号</el-button>
    <el-button type="warning" @click="doRefreshAll" :loading="refreshingAll">
      {{ refreshingAll ? '查询中…' : '全部查询 Tier' }}
    </el-button>
    <span style="margin-left: 8px; font-size: 12px; color: #909399">
      Tier 取自 CognitiveServices quotaTiers（currentTierName）
    </span>

    <el-table :data="accounts" v-loading="loading" style="margin-top: 12px" row-key="id"
      :expand-row-keys="expandedKeys" @expand-change="onExpand">
      <template #empty>
        <el-empty description="暂无 Azure 账号" :image-size="80">
          <el-button type="primary" size="small" @click="openCreate">新建 Azure 账号</el-button>
        </el-empty>
      </template>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div v-if="row._detailLoading" style="padding: 8px 24px; color: #909399">查询中…</div>
          <div v-else-if="row._detailError" style="padding: 8px 24px; color: #f56c6c">{{ row._detailError }}</div>
          <el-table v-else :data="row._subs || []" size="small" style="margin: 4px 24px; width: calc(100% - 48px)">
            <el-table-column prop="display_name" label="订阅名称" min-width="180" />
            <el-table-column prop="subscription_id" label="订阅 ID" min-width="220">
              <template #default="{ row: s }"><span style="font-family: monospace; font-size: 12px">{{ s.subscription_id }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row: s }">
                <el-tag :type="s.state === 'Enabled' ? 'success' : 'danger'" size="small">{{ s.state }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Tier 等级" width="160">
              <template #default="{ row: s }">
                <el-tag v-if="s.tier" type="warning" size="small">{{ s.tier }}</el-tag>
                <span v-else style="color: #c0c4cc">-</span>
              </template>
            </el-table-column>
            <el-table-column label="本月消费" width="140">
              <template #default="{ row: s }">
                <span v-if="s.cost != null">{{ s.cost.toFixed(2) }} {{ s.currency }}</span>
                <span v-else style="color: #c0c4cc">-</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="租户 ID" min-width="200">
        <template #default="{ row }"><span style="font-family: monospace; font-size: 12px">{{ row.tenant_id }}</span></template>
      </el-table-column>
      <el-table-column label="Tier 概览" min-width="200">
        <template #default="{ row }">
          <span v-if="row._tierSummary">{{ row._tierSummary }}</span>
          <span v-else style="color: #c0c4cc">未查询</span>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="120" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="warning" @click="doRefresh(row)" :loading="row._detailLoading">查询 Tier</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑 Azure 账号' : '新建 Azure 账号'"
      width="560px" :style="{ maxWidth: '92vw' }" @closed="resetForm">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 Azure-01" />
        </el-form-item>
        <el-form-item label="租户 ID" prop="tenant_id">
          <el-input v-model="form.tenant_id" placeholder="Directory (tenant) ID" />
        </el-form-item>
        <el-form-item label="客户端 ID" prop="client_id">
          <el-input v-model="form.client_id" placeholder="Application (client) ID" />
        </el-form-item>
        <el-form-item label="客户端密钥" :prop="editingId ? '' : 'client_secret'">
          <el-input v-model="form.client_secret" type="password" show-password
            :placeholder="editingId ? '留空则不修改' : 'Client secret value'" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <div style="font-size: 12px; color: #909399; margin-top: 4px">
        应用需在订阅 IAM 中分配 Reader 角色；密钥 Fernet 加密入库，永不回显。
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm" :loading="submitting">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAzureAccounts, createAzureAccount, updateAzureAccount,
  deleteAzureAccount, refreshAzureAccount, refreshAllAzureAccounts,
} from '../api/client.js'

const accounts = ref([])
const loading = ref(false)
const refreshingAll = ref(false)
const expandedKeys = ref([])
const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const formRef = ref(null)
const form = ref({ name: '', tenant_id: '', client_id: '', client_secret: '', remark: '' })
const formRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  tenant_id: [{ required: true, message: '请输入租户 ID', trigger: 'blur' }],
  client_id: [{ required: true, message: '请输入客户端 ID', trigger: 'blur' }],
  client_secret: [{ required: true, message: '请输入客户端密钥', trigger: 'blur' }],
}

const load = async () => {
  loading.value = true
  try {
    accounts.value = await listAzureAccounts()
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const applyDetail = (row, detail) => {
  row._subs = detail.subscriptions || []
  row._detailError = detail.error || ''
  const tiers = [...new Set((row._subs || []).map((s) => s.tier).filter(Boolean))]
  row._tierSummary = tiers.length ? tiers.join(' / ') : (row._subs?.length ? '无 Tier 数据' : '')
}

const doRefresh = async (row) => {
  row._detailLoading = true
  try {
    const detail = await refreshAzureAccount(row.id)
    applyDetail(row, detail)
    if (detail.error) {
      ElMessage.warning(`「${row.name}」查询异常：${detail.error}`)
    } else {
      ElMessage.success(`「${row.name}」查询完成`)
      if (!expandedKeys.value.includes(row.id)) expandedKeys.value.push(row.id)
    }
  } catch (e) {
    row._detailError = e.response?.data?.detail || e.message
    ElMessage.error('查询失败：' + row._detailError)
  } finally {
    row._detailLoading = false
  }
}

const onExpand = (row, expanded) => {
  if (expanded && !row._subs && !row._detailLoading) doRefresh(row)
}

const doRefreshAll = async () => {
  if (!accounts.value.length) {
    ElMessage.info('暂无账号')
    return
  }
  refreshingAll.value = true
  try {
    const details = await refreshAllAzureAccounts()
    const byId = Object.fromEntries(details.map((d) => [d.id, d]))
    let okCount = 0
    for (const row of accounts.value) {
      const d = byId[row.id]
      if (d) {
        applyDetail(row, d)
        if (!d.error) okCount++
      }
    }
    expandedKeys.value = accounts.value.map((r) => r.id)
    ElMessage.success(`批量查询完成：${okCount}/${accounts.value.length} 成功`)
  } catch (e) {
    ElMessage.error('批量查询失败：' + (e.response?.data?.detail || e.message))
  } finally {
    refreshingAll.value = false
  }
}

const resetForm = () => {
  editingId.value = null
  form.value = { name: '', tenant_id: '', client_id: '', client_secret: '', remark: '' }
}
const openCreate = () => {
  resetForm()
  dialogVisible.value = true
}
const openEdit = (row) => {
  editingId.value = row.id
  form.value = { name: row.name, tenant_id: row.tenant_id, client_id: row.client_id, client_secret: '', remark: row.remark || '' }
  dialogVisible.value = true
}
const submitForm = async () => {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    if (editingId.value) {
      await updateAzureAccount(editingId.value, form.value)
      ElMessage.success('已更新')
    } else {
      await createAzureAccount(form.value)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}
const remove = async (row) => {
  try {
    await ElMessageBox.confirm(`删除 Azure 账号「${row.name}」？`, '确认', { type: 'warning' })
    await deleteAzureAccount(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(load)
</script>
