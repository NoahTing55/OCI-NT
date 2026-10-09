<template>
  <div>
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
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listCfTokens,
  cfListZones, cfListRecords, cfCreateRecord,
  cfUpdateRecord, cfDeleteRecord, cfToggleProxy,
  cfBatchProxy, cfBatchDeleteRecords,
} from '../api/client.js'

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
  tokens.value = await listCfTokens()
  if (tokens.value.length === 1) {
    sel.tokenId = tokens.value[0].id
    await loadZones()
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

onMounted(loadTokens)
</script>
