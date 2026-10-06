<template>
  <div>
    <!-- 换 IP -->
    <el-card header="更换 IP（预留 IP 流程）" style="margin-bottom: 16px">
      <el-form :inline="true" :model="ipForm">
        <el-form-item label="账号">
          <el-select v-model="ipForm.account_id" placeholder="选择账号" style="width: 180px" @change="loadAccountInstances">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="实例">
          <el-select v-model="ipForm.instance_id" placeholder="先选账号" style="width: 280px">
            <el-option
              v-for="i in accountInstances"
              :key="i.instance_id"
              :value="i.instance_id"
              :label="`${i.display_name}（${i.public_ip || '无公网IP'}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="ipForm.release_old">释放旧预留 IP</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="changing" :disabled="!ipForm.instance_id" @click="submitChangeIp">
            开始更换
          </el-button>
        </el-form-item>
      </el-form>
      <div v-if="ipResult" style="font-size: 13px">
        旧 IP：{{ ipResult.old_ip }} → 新 IP：<b>{{ ipResult.new_ip }}</b>
        <span v-if="ipResult.released_old_public_ip_id">（已释放旧预留 IP）</span>
        <div v-if="ipResult.cf_synced && ipResult.cf_synced.length" style="margin-top: 4px">
          CF 同步：
          <el-tag
            v-for="s in ipResult.cf_synced"
            :key="s.domain"
            :type="s.ok ? 'success' : 'danger'"
            size="small"
            style="margin-right: 6px"
          >
            {{ s.domain }} {{ s.ok ? '✓' : '✗ ' + (s.error || '') }}
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- CF Token -->
    <el-card header="Cloudflare Token（加密存储，权限最小化：Zone.DNS 编辑）" style="margin-bottom: 16px">
      <el-button size="small" type="primary" @click="tokenVisible = true">新增 Token</el-button>
      <el-table :data="tokens" size="small" style="margin-top: 8px" border>
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column prop="remark" label="备注" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" @click="removeToken(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-dialog v-model="tokenVisible" title="新增 CF Token" width="460px">
        <el-form :model="tokenForm" label-width="80px">
          <el-form-item label="名称"><el-input v-model="tokenForm.name" placeholder="如 主域名Token" /></el-form-item>
          <el-form-item label="Token">
            <el-input v-model="tokenForm.token" type="password" show-password placeholder="CF API Token" />
          </el-form-item>
          <el-form-item label="备注"><el-input v-model="tokenForm.remark" /></el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="tokenVisible = false">取消</el-button>
          <el-button type="primary" :loading="tokenSaving" @click="submitToken">保存</el-button>
        </template>
      </el-dialog>
    </el-card>

    <!-- 域名绑定 -->
    <el-card header="域名绑定（实例 ↔ 域名，换 IP 后自动同步）" style="margin-bottom: 16px">
      <div style="margin-bottom: 8px">
        <el-button size="small" type="primary" @click="bindVisible = true">新增绑定</el-button>
        <el-button size="small" :loading="checking" @click="runCheck">一致性巡检</el-button>
        <el-button size="small" :loading="syncingAll" @click="runSyncAll">一键同步全部</el-button>
      </div>
      <el-table :data="bindings" size="small" border>
        <el-table-column prop="domain" label="域名" width="200" />
        <el-table-column prop="record_type" label="类型" width="70" />
        <el-table-column prop="instance_name" label="实例" width="160" />
        <el-table-column label="小黄云" width="80">
          <template #default="{ row }">{{ row.proxied ? '开' : '关' }}</template>
        </el-table-column>
        <el-table-column label="自动同步" width="90">
          <template #default="{ row }">{{ row.auto_sync ? '开' : '关' }}</template>
        </el-table-column>
        <el-table-column prop="last_ip" label="上次同步 IP" width="140" />
        <el-table-column prop="last_sync_at" label="上次同步" width="180" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="syncOne(row)">同步</el-button>
            <el-button size="small" type="danger" @click="removeBinding(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="mismatches.length" style="margin-top: 8px">
        <el-alert
          v-for="m in mismatches"
          :key="m.binding_id"
          :title="`不一致：${m.domain}（CF=${m.cf_ip || '无记录'}，本地=${m.last_ip || '未同步'}）${m.error || ''}`"
          type="warning"
          show-icon
          style="margin-bottom: 4px"
        />
      </div>
      <div v-else-if="checkedCount > 0" style="margin-top: 8px; color: #67c23a; font-size: 13px">
        巡检完成：{{ checkedCount }} 个绑定全部一致
      </div>

      <el-dialog v-model="bindVisible" title="新增域名绑定" width="520px">
        <el-form :model="bindForm" label-width="100px">
          <el-form-item label="CF Token">
            <el-select v-model="bindForm.cf_token_id" style="width: 100%">
              <el-option v-for="t in tokens" :key="t.id" :value="t.id" :label="t.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="域名"><el-input v-model="bindForm.domain" placeholder="如 vpn.example.com" /></el-form-item>
          <el-form-item label="记录类型">
            <el-select v-model="bindForm.record_type" style="width: 100%">
              <el-option value="A" label="A" />
              <el-option value="AAAA" label="AAAA" />
            </el-select>
          </el-form-item>
          <el-form-item label="关联账号">
            <el-select v-model="bindForm.account_id" clearable placeholder="可选（用于查实时 IP）" style="width: 100%">
              <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="实例 OCID"><el-input v-model="bindForm.instance_ocid" placeholder="ocid1.instance.oc1.." /></el-form-item>
          <el-form-item label="实例名称"><el-input v-model="bindForm.instance_name" placeholder="备注用" /></el-form-item>
          <el-form-item label="TTL"><el-input-number v-model="bindForm.ttl" :min="60" /></el-form-item>
          <el-form-item label="小黄云"><el-switch v-model="bindForm.proxied" /></el-form-item>
          <el-form-item label="自动同步"><el-switch v-model="bindForm.auto_sync" /></el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="bindVisible = false">取消</el-button>
          <el-button type="primary" :loading="bindSaving" @click="submitBinding">保存</el-button>
        </template>
      </el-dialog>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  listInstances,
  changeIp,
  listCfTokens,
  createCfToken,
  deleteCfToken,
  listBindings,
  createBinding,
  deleteBinding,
  syncBinding,
  cfCheck,
  cfSyncAll,
} from '../api/client.js'

const accounts = ref([])

// ---------- 换 IP ----------
const ipForm = ref({ account_id: null, instance_id: '', release_old: true })
const accountInstances = ref([])
const changing = ref(false)
const ipResult = ref(null)

const loadAccountInstances = async () => {
  ipForm.value.instance_id = ''
  accountInstances.value = []
  if (!ipForm.value.account_id) return
  try {
    const data = await listInstances({ account_id: ipForm.value.account_id })
    accountInstances.value = data.items
  } catch (e) {
    ElMessage.error('加载实例失败：' + (e.response?.data?.detail || e.message))
  }
}

const submitChangeIp = async () => {
  changing.value = true
  ipResult.value = null
  try {
    const r = await changeIp({
      account_id: ipForm.value.account_id,
      instance_id: ipForm.value.instance_id,
      release_old: ipForm.value.release_old,
    })
    ipResult.value = r
    ElMessage.success(`换 IP 成功：${r.old_ip} → ${r.new_ip}`)
  } catch (e) {
    ElMessage.error('换 IP 失败：' + (e.response?.data?.detail || e.message))
  } finally {
    changing.value = false
  }
}

// ---------- CF Token ----------
const tokens = ref([])
const tokenVisible = ref(false)
const tokenSaving = ref(false)
const tokenForm = ref({ name: '', token: '', remark: '' })

const loadTokens = async () => {
  tokens.value = await listCfTokens().catch(() => [])
}

const submitToken = async () => {
  tokenSaving.value = true
  try {
    await createCfToken(tokenForm.value)
    ElMessage.success('已保存（加密入库）')
    tokenVisible.value = false
    tokenForm.value = { name: '', token: '', remark: '' }
    loadTokens()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    tokenSaving.value = false
  }
}

const removeToken = async (row) => {
  try {
    await ElMessageBox.confirm(`删除 Token「${row.name}」？`, '确认', { type: 'warning' })
    await deleteCfToken(row.id)
    ElMessage.success('已删除')
    loadTokens()
  } catch (e) {
    if (e?.response) ElMessage.error('删除失败：' + (e.response.data?.detail || e.message))
  }
}

// ---------- 域名绑定 ----------
const bindings = ref([])
const bindVisible = ref(false)
const bindSaving = ref(false)
const bindForm = ref({
  cf_token_id: null,
  account_id: null,
  instance_ocid: '',
  instance_name: '',
  domain: '',
  record_type: 'A',
  proxied: false,
  ttl: 120,
  auto_sync: true,
})
const mismatches = ref([])
const checkedCount = ref(0)
const checking = ref(false)
const syncingAll = ref(false)

const loadBindings = async () => {
  bindings.value = await listBindings().catch(() => [])
}

const submitBinding = async () => {
  bindSaving.value = true
  try {
    await createBinding(bindForm.value)
    ElMessage.success('绑定已创建（zone 已解析）')
    bindVisible.value = false
    loadBindings()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    bindSaving.value = false
  }
}

const removeBinding = async (row) => {
  try {
    await ElMessageBox.confirm(`删除绑定 ${row.domain}？`, '确认', { type: 'warning' })
    await deleteBinding(row.id)
    ElMessage.success('已删除')
    loadBindings()
  } catch (e) {
    if (e?.response) ElMessage.error('删除失败：' + (e.response.data?.detail || e.message))
  }
}

const syncOne = async (row) => {
  try {
    const r = await syncBinding(row.id)
    ElMessage.success(`${r.domain} 已同步 → ${r.ip}${r.created ? '（新建记录）' : ''}`)
    loadBindings()
  } catch (e) {
    ElMessage.error('同步失败：' + (e.response?.data?.detail || e.message))
  }
}

const runCheck = async () => {
  checking.value = true
  try {
    const r = await cfCheck()
    mismatches.value = r.mismatches
    checkedCount.value = r.checked
    if (!r.mismatches.length) ElMessage.success(`巡检完成：${r.checked} 个绑定全部一致`)
  } catch (e) {
    ElMessage.error('巡检失败：' + (e.response?.data?.detail || e.message))
  } finally {
    checking.value = false
  }
}

const runSyncAll = async () => {
  syncingAll.value = true
  try {
    const r = await cfSyncAll()
    const okN = r.results.filter((x) => x.ok).length
    ElMessage.success(`同步完成：${okN}/${r.results.length} 成功`)
    loadBindings()
  } catch (e) {
    ElMessage.error('同步失败：' + (e.response?.data?.detail || e.message))
  } finally {
    syncingAll.value = false
  }
}

onMounted(async () => {
  accounts.value = await listAccounts().catch(() => [])
  loadTokens()
  loadBindings()
})
</script>
