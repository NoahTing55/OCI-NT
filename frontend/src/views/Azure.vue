<template>
  <div>
    <el-button type="primary" @click="openCreate">新建 Azure 账号</el-button>
    <el-button type="warning" @click="doRefreshAll" :loading="refreshingAll">
      {{ refreshingAll ? '查询中…' : '全部查询 Tier' }}
    </el-button>
    <span style="margin-left: 8px; font-size: 12px; color: #909399">
      Tier 取自 CognitiveServices quotaTiers（currentTierName）
    </span>

    <el-table :data="flatRows" v-loading="loading" style="margin-top: 12px" row-key="_key">
      <template #empty>
        <el-empty description="暂无 Azure 账号" :image-size="80">
          <el-button type="primary" size="small" @click="openCreate">新建 Azure 账号</el-button>
        </el-empty>
      </template>
      <el-table-column prop="account_name" label="账号名称" width="170" />
      <el-table-column prop="display_name" label="订阅名称" min-width="160" />
      <el-table-column label="订阅 ID" min-width="210">
        <template #default="{ row }">
          <span v-if="row.subscription_id" style="font-family: monospace; font-size: 12px">{{ row.subscription_id }}</span>
          <span v-else style="color: #c0c4cc">-</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.state" :type="row.state === 'Enabled' ? 'success' : 'danger'" size="small">{{ row.state }}</el-tag>
          <span v-else style="color: #c0c4cc">-</span>
        </template>
      </el-table-column>
      <el-table-column label="Tier 等级" width="130">
        <template #default="{ row }">
          <el-tag v-if="row.tier" type="warning" size="small">{{ row.tier }}</el-tag>
          <span v-else style="color: #c0c4cc">-</span>
        </template>
      </el-table-column>
      <el-table-column label="本月消费" width="120">
        <template #default="{ row }">
          <span v-if="row.cost != null">{{ row.cost.toFixed(2) }} {{ row.currency }}</span>
          <span v-else style="color: #c0c4cc">-</span>
        </template>
      </el-table-column>
      <el-table-column label="代理" width="110">
        <template #default="{ row }">
          <span v-if="row.proxy_name">{{ row.proxy_name }}</span>
          <span v-else style="color: #c0c4cc">直连</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="warning" @click="doRefresh(row._account)" :loading="row._account._loading">查询 Tier</el-button>
          <el-button size="small" @click="openEdit(row._account)">编辑</el-button>
          <el-button size="small" type="danger" @click="remove(row._account)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑 Azure 账号' : '新建 Azure 账号'"
      width="600px" :style="{ maxWidth: '92vw' }" @closed="resetForm">
      <el-button v-if="!editingId" size="small" style="margin-bottom: 12px" @click="jsonVisible = true">
        从 az ad sp create-for-rbac JSON 粘贴
      </el-button>
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
        <el-form-item label="代理">
          <el-select v-model="form.proxy_id" clearable placeholder="直连" style="width: 100%">
            <el-option v-for="p in availableProxies" :key="p.id" :value="p.id"
              :label="`${p.name} (${p.scheme}://${p.host}:${p.port})`"
              :disabled="p.bound_account_name != null" />
          </el-select>
          <div style="font-size: 12px; color: #909399">已绑定其他账号的代理不可选（一代理一账号）</div>
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

    <el-dialog v-model="jsonVisible" title="从 JSON 粘贴" width="560px" :style="{ maxWidth: '92vw' }">
      <el-input v-model="jsonText" type="textarea" :rows="10"
        placeholder='粘贴 az ad sp create-for-rbac 的输出，如：&#10;{"appId":"...","displayName":"...","password":"...","tenant":"..."}' />
      <template #footer>
        <el-button @click="jsonVisible = false">取消</el-button>
        <el-button type="primary" @click="parseJson">解析填入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAzureAccounts, createAzureAccount, updateAzureAccount,
  deleteAzureAccount, refreshAzureAccount, refreshAllAzureAccounts, listProxies,
} from '../api/client.js'

const accounts = ref([])
const proxies = ref([])
const loading = ref(false)
const refreshingAll = ref(false)
const dialogVisible = ref(false)
const jsonVisible = ref(false)
const jsonText = ref('')
const submitting = ref(false)
const editingId = ref(null)
const formRef = ref(null)
const form = ref({ name: '', tenant_id: '', client_id: '', client_secret: '', remark: '', proxy_id: null })
const formRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  tenant_id: [{ required: true, message: '请输入租户 ID', trigger: 'blur' }],
  client_id: [{ required: true, message: '请输入客户端 ID', trigger: 'blur' }],
  client_secret: [{ required: true, message: '请输入客户端密钥', trigger: 'blur' }],
}

// 拍平：每行 = 一个订阅；未查询的账号占一行
const flatRows = computed(() => {
  const rows = []
  for (const acc of accounts.value) {
    const subs = acc._subs || []
    if (!subs.length) {
      rows.push({
        _key: `acc-${acc.id}`,
        _account: acc,
        account_name: acc.name,
        display_name: acc._detailError ? `查询失败：${acc._detailError}` : '未查询',
        subscription_id: '',
        state: '',
        tier: '',
        cost: null,
        currency: '',
        proxy_name: acc.proxy_name,
      })
    } else {
      subs.forEach((s, i) => {
        rows.push({
          _key: `acc-${acc.id}-sub-${i}`,
          _account: acc,
          account_name: acc.name,
          display_name: s.display_name,
          subscription_id: s.subscription_id,
          state: s.state,
          tier: s.tier,
          cost: s.cost,
          currency: s.currency,
          proxy_name: acc.proxy_name,
        })
      })
    }
  }
  return rows
})

const availableProxies = computed(() => proxies.value)

const load = async () => {
  loading.value = true
  try {
    const [accs, prxs] = await Promise.all([listAzureAccounts(), listProxies()])
    accounts.value = accs
    proxies.value = prxs
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const applyDetail = (acc, detail) => {
  acc._subs = detail.subscriptions || []
  acc._detailError = detail.error || ''
  acc.proxy_name = detail.proxy_name ?? acc.proxy_name
}

const doRefresh = async (acc) => {
  acc._loading = true
  try {
    const detail = await refreshAzureAccount(acc.id)
    applyDetail(acc, detail)
    if (detail.error) {
      ElMessage.warning(`「${acc.name}」查询异常：${detail.error}`)
    } else {
      ElMessage.success(`「${acc.name}」查询完成`)
    }
  } catch (e) {
    acc._detailError = e.response?.data?.detail || e.message
    ElMessage.error('查询失败：' + acc._detailError)
  } finally {
    acc._loading = false
  }
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
    for (const acc of accounts.value) {
      const d = byId[acc.id]
      if (d) {
        applyDetail(acc, d)
        if (!d.error) okCount++
      }
    }
    ElMessage.success(`批量查询完成：${okCount}/${accounts.value.length} 成功`)
  } catch (e) {
    ElMessage.error('批量查询失败：' + (e.response?.data?.detail || e.message))
  } finally {
    refreshingAll.value = false
  }
}

const parseJson = () => {
  try {
    const j = JSON.parse(jsonText.value)
    form.value.name = j.displayName || form.value.name
    form.value.tenant_id = j.tenant || ''
    form.value.client_id = j.appId || ''
    form.value.client_secret = j.password || ''
    jsonVisible.value = false
    jsonText.value = ''
    ElMessage.success('已填入表单')
  } catch (e) {
    ElMessage.error('JSON 解析失败：' + e.message)
  }
}

const resetForm = () => {
  editingId.value = null
  form.value = { name: '', tenant_id: '', client_id: '', client_secret: '', remark: '', proxy_id: null }
}
const openCreate = () => {
  resetForm()
  dialogVisible.value = true
}
const openEdit = (acc) => {
  editingId.value = acc.id
  form.value = {
    name: acc.name, tenant_id: acc.tenant_id, client_id: acc.client_id,
    client_secret: '', remark: acc.remark || '', proxy_id: acc.proxy_id || null,
  }
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
    const payload = { ...form.value }
    if (!payload.proxy_id) payload.proxy_id = null
    if (editingId.value) {
      await updateAzureAccount(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await createAzureAccount(payload)
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
const remove = async (acc) => {
  try {
    await ElMessageBox.confirm(`删除 Azure 账号「${acc.name}」？`, '确认', { type: 'warning' })
    await deleteAzureAccount(acc.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(load)
</script>
