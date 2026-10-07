<template>
  <div>
    <el-button type="primary" @click="openCreate">新建账号</el-button>
    <el-button @click="checkAll" :loading="checkingAll">全部存活检查</el-button>
    <el-button @click="loadSummary" :loading="summaryLoading">刷新摘要</el-button>

    <!-- 账户摘要卡片 -->
    <div v-if="summary.length" class="summary-cards">
      <el-card v-for="s in summary" :key="s.account_id" class="summary-card" shadow="hover" @click="goInstances(s.account_id)">
        <div class="summary-head">
          <span class="summary-name">{{ s.name }}</span>
          <el-tag size="small" type="info">{{ s.region }}</el-tag>
        </div>
        <div class="summary-stats">
          <div class="stat"><div class="stat-num">{{ s.running_count }}<span class="stat-sub">/{{ s.instance_count }}</span></div><div class="stat-label">运行中/总数</div></div>
          <div class="stat"><div class="stat-num">{{ s.ocpu_used }}</div><div class="stat-label">OCPU 已用</div></div>
          <div class="stat"><div class="stat-num">{{ s.memory_used_gb }}<span class="stat-unit">G</span></div><div class="stat-label">内存已用</div></div>
        </div>
        <div class="summary-quotas">
          <div v-for="q in quotaRows(s)" :key="q.key" class="quota-row">
            <span class="quota-label">{{ q.label }}</span>
            <el-progress :percentage="q.pct" :color="q.color" :show-text="false" class="quota-bar" />
            <span class="quota-text">{{ q.text }}</span>
          </div>
        </div>
      </el-card>
    </div>
    <el-empty v-else-if="summaryLoaded" description="暂无摘要数据" :image-size="60" style="margin: 12px 0" />

    <el-table :data="accounts" v-loading="loading" style="margin-top: 12px" border stripe size="small" class="acct-table">
      <!-- 🛡️ 代理绑定状态：点击快速配置代理 -->
      <el-table-column width="52" align="center">
        <template #header><span title="绑定代理" style="opacity: .55">🛡️</span></template>
        <template #default="{ row }">
          <span class="shield-btn" :class="{ bound: !!row.proxy_id }"
            :title="row.proxy ? `已绑定：${row.proxy.name}，点击配置` : '未绑定代理，点击配置'"
            @click="openBind(row)">🛡️</span>
        </template>
      </el-table-column>
      <!-- 别名：点击单元格直接改 -->
      <el-table-column label="别名" min-width="110" class-name="alias-col">
        <template #default="{ row }">
          <el-input v-if="isEditing(row.id, 'name')" v-model="cellVal" size="small" ref="cellInputRef"
            @keyup.enter="saveCell(row, 'name')" @blur="saveCell(row, 'name')" />
          <span v-else class="cell-editable" @click="startEdit(row, 'name')" :title="'点击修改：' + row.name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="region" label="区域" width="140" />
      <!-- 成本：点击单元格直接改 -->
      <el-table-column label="成本" width="100" align="center">
        <template #default="{ row }">
          <el-input-number v-if="isEditing(row.id, 'cost')" v-model="cellVal" size="small" :min="0" :precision="2"
            @keyup.enter="saveCell(row, 'cost')" @blur="saveCell(row, 'cost')" ref="cellInputRef" style="width: 96px" />
          <span v-else class="cell-editable" @click="startEdit(row, 'cost')" title="点击修改成本">{{ fmtCost(row.cost) }}</span>
        </template>
      </el-table-column>
      <!-- 存活天数徽标 -->
      <el-table-column label="存活天数" width="90" align="center">
        <template #default="{ row }"><el-tag size="small" class="days-chip">{{ aliveDays(row) }}</el-tag></template>
      </el-table-column>
      <!-- 抢机任务状态 -->
      <el-table-column label="抢机任务" width="110" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.snipe_task_status === 'running'" type="success" size="small" class="task-badge"><span class="spin-dot"></span>抢机中</el-tag>
          <el-tag v-else-if="row.snipe_task_status === 'paused'" type="warning" size="small">已暂停</el-tag>
          <el-tag v-else type="info" size="small">无</el-tag>
        </template>
      </el-table-column>
      <!-- 账号类型：免费/试用/升级/未知（对标 OCI-Start） -->
      <el-table-column label="账号类型" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.account_type === 'free'" type="success" size="small">个人免费号</el-tag>
          <el-tag v-else-if="row.account_type === 'trial'" type="warning" size="small">个人试用号</el-tag>
          <el-tag v-else-if="row.account_type === 'upgraded'" type="primary" size="small">个人升级号</el-tag>
          <el-tag v-else-if="row.account_type === 'paid'" type="primary" size="small">付费</el-tag>
          <el-tag v-else type="info" size="small">未知</el-tag>
        </template>
      </el-table-column>
      <!-- 实例数：点击跳转筛选 -->
      <el-table-column label="实例数" width="80" align="center">
        <template #default="{ row }">
          <el-link type="primary" @click="goInstances(row.id)">{{ row.instance_count ?? 0 }}</el-link>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="STATUS[row.status]?.[1] || ''" size="small">{{ STATUS[row.status]?.[0] || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="110" align="center">
        <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
      </el-table-column>
      <!-- 操作收进下拉菜单 -->
      <el-table-column label="操作" width="70" align="center" fixed="right">
        <template #default="{ row }">
          <el-dropdown trigger="click" @command="(cmd) => handleOp(cmd, row)">
            <el-button size="small">···</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="create">创建实例</el-dropdown-item>
                <el-dropdown-item command="edit">编辑</el-dropdown-item>
                <el-dropdown-item command="bind">绑定代理</el-dropdown-item>
                <el-dropdown-item command="check">存活检查</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建账号 -->
    <el-dialog v-model="createVisible" title="新建账号" width="560px">
      <el-tabs v-model="createTab">
        <el-tab-pane label="手动填写" name="manual">
      <el-form :model="form" label-width="110px">
        <el-form-item label="别名"><el-input v-model="form.name" placeholder="如 香港-01" /></el-form-item>
        <el-form-item label="Tenancy OCID"><el-input v-model="form.tenancy_ocid" placeholder="ocid1.tenancy.oc1.." /></el-form-item>
        <el-form-item label="User OCID"><el-input v-model="form.user_ocid" placeholder="ocid1.user.oc1.." /></el-form-item>
        <el-form-item label="指纹"><el-input v-model="form.fingerprint" placeholder="aa:bb:cc:.." /></el-form-item>
        <el-form-item label="私钥 PEM">
          <el-input v-model="form.private_key" type="textarea" :rows="5" placeholder="-----BEGIN PRIVATE KEY-----" show-password />
          <div style="font-size:12px;color:#909399">只在提交瞬间传输，服务端加密入库，永不回显</div>
        </el-form-item>
        <el-form-item label="区域">
          <el-select v-model="form.region" style="width:100%">
            <el-option v-for="r in REGIONS" :key="r" :value="r" :label="r" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
        </el-tab-pane>
        <el-tab-pane label="配置文件导入" name="import">
          <el-form label-width="110px">
            <el-form-item label="config 内容">
              <el-input v-model="importForm.config" type="textarea" :rows="7"
                placeholder="粘贴 ~/.oci/config 内容，如：&#10;[DEFAULT]&#10;user=ocid1.user.oc1...&#10;fingerprint=aa:bb:cc...&#10;tenancy=ocid1.tenancy.oc1...&#10;region=ap-seoul-1" />
            </el-form-item>
            <el-form-item label="私钥 PEM">
              <el-input v-model="importForm.privateKey" type="textarea" :rows="5"
                placeholder="-----BEGIN PRIVATE KEY-----" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="parseConfig">解析并填入</el-button>
              <span style="font-size:12px;color:#909399;margin-left:8px">解析 [DEFAULT] 段的 user / fingerprint / tenancy / region</span>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
      <el-form :model="form" label-width="110px" style="margin-top: 4px">
        <el-form-item label="绑定代理">
          <el-select v-model="form.proxy_id" placeholder="选择代理" style="width: 100%">
            <el-option :value="null" label="直连（不使用代理）" />
            <el-option
              v-for="p in proxies"
              :key="p.id"
              :value="p.id"
              :disabled="!!p.bound_account_name"
              :label="p.bound_account_name ? `${p.name}（已绑定：${p.bound_account_name}）` : `${p.name}（${p.scheme}://${p.host}:${p.port}）`"
            >
              <span>{{ p.name }}（{{ p.scheme }}://{{ p.host }}:{{ p.port }}）</span>
              <span v-if="p.bound_account_name" style="float: right; color: #e6a23c; font-size: 12px">已绑定：{{ p.bound_account_name }}</span>
              <span v-else style="float: right; color: #67c23a; font-size: 12px">{{ p.status === 'ok' ? `延迟 ${p.latency_ms ?? '-'}ms` : '未使用' }}</span>
            </el-option>
          </el-select>
          <div style="font-size:12px;color:#909399">默认选中第一个未使用的代理；一代理只能绑一个账号</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate" :loading="submitting">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑账号（别名/区域/备注，不涉及密钥） -->
    <el-dialog v-model="editVisible" title="编辑账号" width="480px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="别名"><el-input v-model="editForm.name" placeholder="如 香港-01" /></el-form-item>
        <el-form-item label="区域">
          <el-select v-model="editForm.region" style="width:100%">
            <el-option v-for="r in REGIONS" :key="r" :value="r" :label="r" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.remark" /></el-form-item>
        <el-form-item label="注册时间">
          <el-date-picker v-model="editForm.registered_at" type="date" placeholder="账号真实注册日期（空则用添加时间）"
            style="width: 100%" value-format="YYYY-MM-DD" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit" :loading="editSubmitting">保存</el-button>
      </template>
    </el-dialog>

    <!-- 绑定代理 -->
    <el-dialog v-model="bindVisible" title="绑定代理（一账号一代理）" width="420px">
      <el-select v-model="bindProxyId" placeholder="选择代理（清空=解绑）" clearable style="width:100%">
        <el-option v-for="p in proxies" :key="p.id" :value="p.id" :label="`${p.name}（${p.scheme}://${p.host}:${p.port}）`" />
      </el-select>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" @click="submitBind" :loading="binding">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listAccounts, createAccount, updateAccount, deleteAccount, bindProxy, checkAccount, checkAllAccounts, listProxies, getAccountSummary } from '../api/client.js'

const router = useRouter()
const REGIONS = ['ap-seoul-1', 'ap-tokyo-1', 'ap-singapore-1', 'ap-osaka-1', 'us-phoenix-1', 'us-ashburn-1', 'eu-frankfurt-1']
const STATUS = {
  healthy: ['正常', 'success'],
  key_invalid: ['密钥失效', 'danger'],
  forbidden: ['权限不足', 'warning'],
  not_found: ['用户不存在', 'warning'],
  network_error: ['网络异常', 'info'],
  unknown_error: ['未知异常', 'danger'],
  unchecked: ['未检查', ''],
}

const accounts = ref([])
const proxies = ref([])
const loading = ref(false)
const checkingAll = ref(false)
const createVisible = ref(false)
const submitting = ref(false)
const bindVisible = ref(false)
const binding = ref(false)
const bindProxyId = ref(null)
const bindAccount = ref(null)
const form = ref({ name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '', proxy_id: null })
// 默认选中第一个未使用的代理；没有可用时为 null（直连）
const defaultProxyId = () => {
  const free = proxies.value.find((p) => !p.bound_account_name)
  return free ? free.id : null
}

// ---------- 编辑账号（别名/区域/备注） ----------
const editVisible = ref(false)
const editSubmitting = ref(false)
const editId = ref(null)
const editForm = ref({ name: '', region: '', remark: '', registered_at: '' })

// ---------- 配置文件导入（纯前端解析，零后端改动） ----------
const createTab = ref('manual')
const importForm = ref({ config: '', privateKey: '' })

// 简单 ini 解析：取 [DEFAULT]（无段头时按整段）中的 key=value
const parseConfig = () => {
  const text = importForm.value.config.trim()
  if (!text) return ElMessage.error('请先粘贴 ~/.oci/config 内容')
  const kv = {}
  let inDefault = !/^\s*\[/m.test(text) // 无段头则整段视为 DEFAULT
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#') || t.startsWith(';')) continue
    const sec = t.match(/^\[(.+)\]$/)
    if (sec) { inDefault = sec[1].trim().toUpperCase() === 'DEFAULT'; continue }
    if (!inDefault) continue
    const m = t.match(/^([^=]+?)\s*=\s*(.+?)\s*$/)
    if (m) kv[m[1].trim().toLowerCase()] = m[2].trim()
  }
  const missing = ['user', 'fingerprint', 'tenancy'].filter((k) => !kv[k])
  if (missing.length) return ElMessage.error('解析失败，缺少字段：' + missing.join('、'))
  form.value.user_ocid = kv.user
  form.value.fingerprint = kv.fingerprint
  form.value.tenancy_ocid = kv.tenancy
  if (kv.region) {
    // region 可能是 ap-seoul-1 或 oc1.ap-seoul-1 格式，取最后一段
    const r = kv.region.split('.').pop()
    form.value.region = REGIONS.includes(r) ? r : kv.region
  }
  if (importForm.value.privateKey.trim()) {
    form.value.private_key = importForm.value.privateKey.trim()
  }
  if (!form.value.name && kv.tenancy) {
    form.value.name = 'oci-' + kv.tenancy.replace(/[^a-zA-Z0-9]/g, '').slice(-6)
  }
  createTab.value = 'manual'
  ElMessage.success('已解析并填入，请检查后保存')
}

const load = async () => {
  loading.value = true
  try {
    const [a, p] = await Promise.all([listAccounts(), listProxies()])
    accounts.value = a
    proxies.value = p
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ---------- 账户摘要 ----------
const summary = ref([])
const summaryLoading = ref(false)
const summaryLoaded = ref(false)
const QUOTA_META = [
  { key: 'e2', label: 'E2 配额' },
  { key: 'a1', label: 'ARM 配额' },
  { key: 'e5', label: 'E5 配额' },
]
// 配额行：计算进度条百分比和显示文本
const quotaRows = (s) => {
  return QUOTA_META.map((m) => {
    const q = (s.quotas || {})[m.key] || {}
    const avail = q.available, used = q.used
    if (avail == null || used == null) {
      return { key: m.key, label: m.label, pct: 0, color: '#dcdfe6', text: '未知' }
    }
    const total = avail + used
    const pct = total > 0 ? Math.round((used / total) * 100) : 0
    const color = pct >= 90 ? '#f56c6c' : pct >= 70 ? '#e6a23c' : '#67c23a'
    return { key: m.key, label: m.label, pct, color, text: `可用 ${avail}/${total}` }
  })
}
const loadSummary = async () => {
  summaryLoading.value = true
  try {
    summary.value = await getAccountSummary()
    summaryLoaded.value = true
  } catch (e) {
    ElMessage.error('摘要加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    summaryLoading.value = false
  }
}
// 卡片点击跳转到实例运维（按账号筛选）
const goInstances = (accountId) => {
  router.push({ path: '/instances', query: { account_id: accountId } })
}
// 跳转抢机页并预选账号（Sniper.vue 的 onMounted 会读取 account_id 自动打开新建对话框）
const goCreateInstance = (accountId) => {
  router.push({ path: '/sniper', query: { account_id: accountId } })
}
// ---------- 单元格点击编辑（别名/成本，OCI-Start 式：点击变输入框，回车/失焦保存） ----------
const editingCell = ref({ id: null, field: null })
const cellVal = ref('')
const cellInputRef = ref(null)
const isEditing = (id, field) => editingCell.value.id === id && editingCell.value.field === field
const startEdit = (row, field) => {
  editingCell.value = { id: row.id, field }
  cellVal.value = field === 'cost' ? Number(row.cost ?? 0) : (row[field] ?? '')
  nextTick(() => { try { cellInputRef.value?.focus() } catch (e) {} })
}
const cancelEdit = () => { editingCell.value = { id: null, field: null } }
const saveCell = async (row, field) => {
  if (!isEditing(row.id, field)) return  // 回车+失焦会触发两次，第二次直接返回
  const v = field === 'cost' ? Number(cellVal.value) : String(cellVal.value).trim()
  cancelEdit()
  if (field === 'name') {
    if (!v) return ElMessage.error('别名不能为空')
    if (v === row.name) return
  } else if (field === 'cost') {
    if (Number(row.cost ?? 0) === v) return
  }
  try {
    await updateAccount(row.id, { [field]: v })
    ElMessage.success('已保存')
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  }
}
// 成本格式化：保留 2 位小数
const fmtCost = (v) => Number(v ?? 0).toFixed(2)
// 操作下拉菜单分发
const handleOp = (cmd, row) => {
  if (cmd === 'create') goCreateInstance(row.id)
  else if (cmd === 'edit') openEdit(row)
  else if (cmd === 'bind') openBind(row)
  else if (cmd === 'check') checkOne(row)
  else if (cmd === 'delete') remove(row)
}
// 存活天数：按自然日计算（避免时区/小时差导致少算一天）
const aliveDays = (row) => {
  const base = row.registered_at || row.created_at
  if (!base) return '-'
  const s = new Date(base)
  const n = new Date()
  const sd = new Date(s.getFullYear(), s.getMonth(), s.getDate())
  const nd = new Date(n.getFullYear(), n.getMonth(), n.getDate())
  const d = Math.round((nd - sd) / 86400000)
  return (d < 0 ? 0 : d) + ' 天'
}
// 创建时间只显示日期
const fmtDate = (v) => {
  if (!v) return '-'
  return String(v).slice(0, 10)
}

const openCreate = () => {
  form.value.proxy_id = defaultProxyId()
  createTab.value = 'manual'
  createVisible.value = true
}

const submitCreate = async () => {
  submitting.value = true
  try {
    await createAccount(form.value)
    ElMessage.success('账号已创建（私钥已加密存储）')
    createVisible.value = false
    form.value = { name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '', proxy_id: null }
    load()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

const openEdit = (row) => {
  editId.value = row.id
  editForm.value = { name: row.name || '', region: row.region || '', remark: row.remark || '',
    registered_at: row.registered_at ? row.registered_at.slice(0, 10) : '' }
  editVisible.value = true
}

const submitEdit = async () => {
  if (!editForm.value.name.trim()) return ElMessage.error('别名不能为空')
  editSubmitting.value = true
  try {
    await updateAccount(editId.value, {
      name: editForm.value.name.trim(),
      region: editForm.value.region,
      remark: editForm.value.remark,
      // 空字符串表示清空（后端用 fields_set 区分"没传"和"清空"）
      ...(editForm.value.registered_at !== undefined ? { registered_at: editForm.value.registered_at || null } : {}),
    })
    ElMessage.success('已保存')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    editSubmitting.value = false
  }
}

const remove = async (row) => {  try {
    await ElMessageBox.confirm(`删除账号「${row.name}」？`, '确认', { type: 'warning' })
    await deleteAccount(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

const checkOne = async (row) => {
  row._checking = true
  try {
    const r = await checkAccount(row.id)
    ElMessage({ message: `${r.name}：${STATUS[r.status]?.[0] || r.status}（${r.message}）`, type: r.status === 'healthy' ? 'success' : 'warning' })
    load()
  } catch (e) {
    ElMessage.error('检查失败：' + (e.response?.data?.detail || e.message))
  } finally {
    row._checking = false
  }
}

const checkAll = async () => {
  checkingAll.value = true
  try {
    const results = await checkAllAccounts()
    const bad = results.filter((r) => r.status !== 'healthy').length
    ElMessage({ message: `检查完成：${results.length} 个账号，${bad} 个异常`, type: bad ? 'warning' : 'success' })
    load()
  } catch (e) {
    ElMessage.error('检查失败：' + (e.response?.data?.detail || e.message))
  } finally {
    checkingAll.value = false
  }
}

const openBind = (row) => {
  bindAccount.value = row
  bindProxyId.value = row.proxy_id
  bindVisible.value = true
}

const submitBind = async () => {
  binding.value = true
  try {
    await bindProxy(bindAccount.value.id, bindProxyId.value ?? null)
    ElMessage.success('绑定已更新')
    bindVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('绑定失败：' + (e.response?.data?.detail || e.message))
  } finally {
    binding.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.summary-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}
.summary-card {
  width: 300px;
  cursor: pointer;
}
.summary-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.summary-name {
  font-weight: 600;
  font-size: 15px;
}
.summary-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 10px;
}
.stat-num {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}
.stat-sub, .stat-unit {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.quota-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.quota-label {
  font-size: 12px;
  color: #606266;
  width: 52px;
  flex-shrink: 0;
}
.quota-bar {
  flex: 1;
}
.quota-text {
  font-size: 12px;
  color: #909399;
  width: 86px;
  text-align: right;
  flex-shrink: 0;
}

/* 表格紧凑精致 */
.acct-table {
  font-size: 13px;
}
/* 🛡️ 代理盾牌：未绑定灰色，已绑定蓝色 */
.shield-btn {
  cursor: pointer;
  font-size: 17px;
  filter: grayscale(1);
  opacity: .45;
  display: inline-block;
  transition: transform .15s;
}
.shield-btn.bound {
  filter: none;
  opacity: 1;
}
.shield-btn:hover {
  transform: scale(1.2);
}
/* 可点击编辑的单元格 */
.cell-editable {
  cursor: pointer;
  border-bottom: 1px dashed #c0c4cc;
}
.cell-editable:hover {
  color: #409eff;
  border-color: #409eff;
}
/* 存活天数徽标 */
.days-chip {
  font-weight: 600;
}
/* 抢机中：旋转圆点 */
.spin-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #67c23a;
  margin-right: 5px;
  vertical-align: 1px;
  animation: spinPulse 1.1s linear infinite;
}
@keyframes spinPulse {
  0% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.7); }
  100% { opacity: 1; transform: scale(1); }
}

/* 别名列紧凑间距 */
.alias-col .cell { padding-left: 4px; padding-right: 4px; }
</style>
