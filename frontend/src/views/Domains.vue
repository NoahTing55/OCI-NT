<template>
  <div>
    <el-tabs type="border-card" v-model="activeTab">
      <!-- Tab 1: DNS 记录 -->
      <el-tab-pane label="DNS 记录" name="dns">
        <!-- 选择器 -->
        <el-card style="margin-bottom: 16px">
          <el-form :inline="true" :model="sel">
            <el-form-item label="CF Token">
              <el-select v-model="sel.tokenId" placeholder="选择 Token" style="width: 200px" @change="onTokenChange">
                <el-option v-for="t in tokens" :key="t.id" :value="t.id" :label="t.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="域名">
              <el-select v-model="sel.zoneId" placeholder="先选 Token" style="width: 260px" @change="loadRecords" :loading="zonesLoading">
                <el-option v-for="z in zones" :key="z.id" :value="z.id" :label="z.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="类型">
              <el-select v-model="sel.type" style="width: 120px" @change="loadRecords" clearable placeholder="全部">
                <el-option label="A" value="A" />
                <el-option label="AAAA" value="AAAA" />
                <el-option label="CNAME" value="CNAME" />
                <el-option label="TXT" value="TXT" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :disabled="!sel.zoneId" @click="openAdd">新增记录</el-button>
              <el-button :disabled="!sel.zoneId" @click="loadRecords">刷新</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 记录列表 -->
        <el-card header="DNS 记录">
          <div style="margin-bottom: 12px" v-if="selected.length">
            <el-button size="small" type="warning" @click="batchProxy(true)">批量开启 CDN</el-button>
            <el-button size="small" @click="batchProxy(false)">批量关闭 CDN</el-button>
            <el-button size="small" type="danger" @click="batchDelete">批量删除</el-button>
            <span style="margin-left: 8px; font-size: 12px; color: #909399">已选 {{ selected.length }} 条</span>
          </div>
          <el-table :data="records" stripe style="width: 100%" v-loading="recordsLoading" @selection-change="onSelect">
            <el-table-column type="selection" width="45" />
            <el-table-column prop="type" label="类型" width="80" />
            <el-table-column prop="name" label="记录名" min-width="200" show-overflow-tooltip />
            <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
            <el-table-column label="CDN" width="90" align="center">
              <template #default="{ row }">
                <el-switch
                  v-model="row.proxied"
                  :disabled="!canProxy(row.type)"
                  @change="(v) => toggleProxy(row, v)"
                  title="小黄云"
                />
              </template>
            </el-table-column>
            <el-table-column prop="ttl" label="TTL" width="90">
              <template #default="{ row }">{{ row.ttl === 1 ? '自动' : row.ttl }}</template>
            </el-table-column>
            <el-table-column label="操作" width="140" align="center">
              <template #default="{ row }">
                <el-button size="small" @click="openEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" @click="removeRecord(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!records.length && !recordsLoading && sel.zoneId" description="暂无记录" />
        </el-card>

        <!-- 新增/编辑对话框 -->
        <el-dialog v-model="dlgVisible" :title="editing ? '编辑 DNS 记录' : '新增 DNS 记录'" width="520px">
          <el-form :model="form" label-width="90px">
            <el-form-item label="类型">
              <el-select v-model="form.type" style="width: 100%">
                <el-option label="A" value="A" />
                <el-option label="AAAA" value="AAAA" />
                <el-option label="CNAME" value="CNAME" />
                <el-option label="TXT" value="TXT" />
              </el-select>
            </el-form-item>
            <el-form-item label="记录名">
              <el-input v-model="form.name" placeholder="如 www，@ 表示根域名" />
            </el-form-item>
            <el-form-item label="内容">
              <el-input v-model="form.content" :placeholder="contentPlaceholder" />
            </el-form-item>
            <el-form-item label="TTL">
              <el-input-number v-model="form.ttl" :min="1" :max="86400" style="width: 100%" />
              <div style="font-size: 12px; color: #909399">填 1 为自动（仅小黄云开启时有效），手动最小 60</div>
            </el-form-item>
            <el-form-item label="CDN 小黄云" v-if="canProxy(form.type)">
              <el-switch v-model="form.proxied" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="dlgVisible = false">取消</el-button>
            <el-button type="primary" :loading="saving" @click="saveRecord">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- Tab 2: Token 管理 -->
      <el-tab-pane label="Token 管理" name="tokens">
        <el-card header="Cloudflare Token（加密存储，权限最小化：Zone.DNS 编辑）">
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
      </el-tab-pane>

      <!-- Tab 3: 域名绑定 -->
      <el-tab-pane label="域名绑定" name="bindings">
        <el-card header="域名绑定（实例 ↔ 域名，换 IP 后自动同步）">
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
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  listCfTokens,
  createCfToken,
  deleteCfToken,
  listBindings,
  createBinding,
  deleteBinding,
  syncBinding,
  cfCheck,
  cfSyncAll,
  cfListZones, cfListRecords, cfCreateRecord,
  cfUpdateRecord, cfDeleteRecord, cfToggleProxy,
  cfBatchProxy, cfBatchDeleteRecords,
} from '../api/client.js'

const activeTab = ref('dns')
const accounts = ref([])

// ---------- DNS 记录 ----------
const tokens = ref([])
const zones = ref([])
const records = ref([])
const selected = ref([])
const zonesLoading = ref(false)
const recordsLoading = ref(false)
const saving = ref(false)
const dlgVisible = ref(false)
const editing = ref(null)

const sel = reactive({ tokenId: null, zoneId: '', type: '' })
const form = reactive({ type: 'A', name: '', content: '', ttl: 120, proxied: false })

const canProxy = (t) => ['A', 'AAAA', 'CNAME'].includes(t)
const contentPlaceholder = computed(() => {
  switch (form.type) {
    case 'A': return '如 1.2.3.4'
    case 'AAAA': return '如 2001:db8::1'
    case 'CNAME': return '如 target.example.com'
    case 'TXT': return '如 v=spf1 ...'
    default: return ''
  }
})
const curZoneName = computed(() => (zones.value.find((z) => z.id === sel.zoneId) || {}).name || '')

const loadTokens = async () => {
  try {
    tokens.value = await listCfTokens()
    if (tokens.value.length === 1) {
      sel.tokenId = tokens.value[0].id
      await loadZones()
    }
  } catch (e) {
    tokens.value = []
  }
}
const onTokenChange = async () => {
  sel.zoneId = ''
  records.value = []
  await loadZones()
}
const loadZones = async () => {
  if (!sel.tokenId) return
  zonesLoading.value = true
  try {
    zones.value = await cfListZones(sel.tokenId)
    if (zones.value.length === 1) {
      sel.zoneId = zones.value[0].id
      await loadRecords()
    }
  } catch (e) {
    ElMessage.error('获取域名列表失败：' + (e.response?.data?.detail || e.message))
  } finally {
    zonesLoading.value = false
  }
}
const loadRecords = async () => {
  if (!sel.tokenId || !sel.zoneId) return
  recordsLoading.value = true
  try {
    records.value = await cfListRecords(sel.zoneId, sel.tokenId, sel.type)
  } catch (e) {
    ElMessage.error('获取 DNS 记录失败：' + (e.response?.data?.detail || e.message))
  } finally {
    recordsLoading.value = false
  }
}
const onSelect = (rows) => { selected.value = rows }

const fullName = (name) => {
  // @ 或已是完整域名则直接用，否则拼 zone
  if (name === '@' || name.endsWith('.' + curZoneName.value) || name === curZoneName.value) return name
  return `${name}.${curZoneName.value}`
}

const openAdd = () => {
  editing.value = null
  Object.assign(form, { type: 'A', name: '', content: '', ttl: 120, proxied: false })
  dlgVisible.value = true
}
const openEdit = (row) => {
  editing.value = row
  // 显示相对名（去掉 zone 后缀）
  let name = row.name
  if (name === curZoneName.value) name = '@'
  else if (name.endsWith('.' + curZoneName.value)) name = name.slice(0, -(curZoneName.value.length + 1))
  Object.assign(form, { type: row.type, name, content: row.content, ttl: row.ttl, proxied: row.proxied })
  dlgVisible.value = true
}
const saveRecord = async () => {
  if (!form.name.trim() || !form.content.trim()) {
    ElMessage.warning('记录名和内容不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      cf_token_id: sel.tokenId,
      type: form.type,
      name: fullName(form.name.trim()),
      content: form.content.trim(),
      ttl: form.ttl,
      proxied: form.proxied,
    }
    if (editing.value) {
      payload.zone_id = sel.zoneId
      await cfUpdateRecord(editing.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await cfCreateRecord(sel.zoneId, payload)
      ElMessage.success('已创建')
    }
    dlgVisible.value = false
    await loadRecords()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}
const removeRecord = async (row) => {
  await ElMessageBox.confirm(`删除记录 ${row.type} ${row.name}？`, '确认', { type: 'warning' })
  try {
    await cfDeleteRecord(row.id, sel.tokenId, sel.zoneId)
    ElMessage.success('已删除')
    await loadRecords()
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}
const toggleProxy = async (row, v) => {
  try {
    await cfToggleProxy(row.id, { cf_token_id: sel.tokenId, zone_id: sel.zoneId, proxied: v })
    ElMessage.success(v ? 'CDN 已开启' : 'CDN 已关闭')
  } catch (e) {
    row.proxied = !v // 回滚
    ElMessage.error('切换失败：' + (e.response?.data?.detail || e.message))
  }
}
const batchProxy = async (v) => {
  try {
    const r = await cfBatchProxy({
      cf_token_id: sel.tokenId, zone_id: sel.zoneId,
      record_ids: selected.value.map((x) => x.id), proxied: v,
    })
    const ok = r.results.filter((x) => x.ok).length
    ElMessage.success(`批量${v ? '开启' : '关闭'} CDN：成功 ${ok}/${r.results.length}`)
    await loadRecords()
  } catch (e) {
    ElMessage.error('批量操作失败：' + (e.response?.data?.detail || e.message))
  }
}
const batchDelete = async () => {
  await ElMessageBox.confirm(`删除选中的 ${selected.value.length} 条记录？`, '确认', { type: 'warning' })
  try {
    const r = await cfBatchDeleteRecords(sel.zoneId, {
      cf_token_id: sel.tokenId,
      record_ids: selected.value.map((x) => x.id),
    })
    const ok = r.results.filter((x) => x.ok).length
    ElMessage.success(`批量删除：成功 ${ok}/${r.results.length}`)
    await loadRecords()
  } catch (e) {
    ElMessage.error('批量删除失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- CF Token ----------
const tokenVisible = ref(false)
const tokenSaving = ref(false)
const tokenForm = ref({ name: '', token: '', remark: '' })

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
  await loadTokens()
  loadBindings()
})
</script>
