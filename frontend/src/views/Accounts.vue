<template>
  <div>
    <el-button type="primary" @click="createVisible = true">新建账号</el-button>
    <el-button @click="checkAll" :loading="checkingAll">全部存活检查</el-button>

    <el-table :data="accounts" v-loading="loading" style="margin-top: 12px" border>
      <el-table-column prop="name" label="别名" width="140" />
      <el-table-column prop="region" label="区域" width="150" />
      <el-table-column label="绑定代理" width="160">
        <template #default="{ row }">{{ row.proxy ? row.proxy.name : '直连' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="STATUS[row.status]?.[1] || ''">{{ STATUS[row.status]?.[0] || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_check_at" label="最后检查" width="180">
        <template #default="{ row }">{{ row.last_check_at || '-' }}</template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" />
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="checkOne(row)" :loading="row._checking">检查</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="openBind(row)">绑定代理</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listAccounts, createAccount, updateAccount, deleteAccount, bindProxy, checkAccount, checkAllAccounts, listProxies } from '../api/client.js'

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
const form = ref({ name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '' })

// ---------- 编辑账号（别名/区域/备注） ----------
const editVisible = ref(false)
const editSubmitting = ref(false)
const editId = ref(null)
const editForm = ref({ name: '', region: '', remark: '' })

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

const submitCreate = async () => {
  submitting.value = true
  try {
    await createAccount(form.value)
    ElMessage.success('账号已创建（私钥已加密存储）')
    createVisible.value = false
    form.value = { name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '' }
    load()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

const openEdit = (row) => {
  editId.value = row.id
  editForm.value = { name: row.name || '', region: row.region || '', remark: row.remark || '' }
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
