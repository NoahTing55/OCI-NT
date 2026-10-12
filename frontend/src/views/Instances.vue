<template>
  <div>
    <el-form :inline="true" :model="filters">
      <el-form-item label="账号">
        <el-select v-model="filters.account_id" clearable placeholder="全部" style="width: 160px">
          <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="区域">
        <el-select v-model="filters.region" clearable placeholder="全部" style="width: 180px">
          <el-option v-for="r in regionOptions" :key="r.value" :value="r.value" :label="r.label" />
        </el-select>
      </el-form-item>
      <el-form-item label="搜索">
        <el-input v-model="filters.keyword" clearable placeholder="名称/IP" style="width: 180px" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="filters.state" clearable placeholder="全部" style="width: 150px">
          <el-option v-for="s in STATES" :key="s" :value="s" :label="s" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="() => load(true, true)" :loading="loading">刷新</el-button>
      </el-form-item>
    </el-form>

    <div v-if="errors.length" style="margin-bottom: 8px">
      <el-alert
        v-for="e in errors"
        :key="e.account_id"
        :title="`账号 ${e.name} 查询失败：${e.error}`"
        type="warning"
        show-icon
        style="margin-bottom: 4px"
      />
    </div>

    <div style="margin: 8px 0; display: flex; align-items: center">
      <el-button size="small" :disabled="!selected.length" @click="runBatch('power_on')">批量开机</el-button>
      <el-button size="small" :disabled="!selected.length" @click="runBatch('power_off')">批量关机</el-button>
      <el-button size="small" :disabled="!selected.length" @click="runBatch('reboot')">批量重启</el-button>
      <el-button size="small" type="danger" :disabled="!selected.length" @click="runBatch('terminate')">批量终止</el-button>
      <span style="margin-left: 8px; color: #909399; font-size: 12px">已选 {{ selected.length }} 台</span>
      <span style="flex: 1"></span>
      <el-radio-group v-model="viewMode" size="small">
        <el-radio-button value="table">表格</el-radio-button>
        <el-radio-button value="card">卡片</el-radio-button>
      </el-radio-group>
    </div>

    <el-table v-if="viewMode === 'table'" :data="pagedInstances" v-loading="loading" size="small" style="width: 100%" @selection-change="selected = $event">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="account_name" label="账号" width="120" />
      <el-table-column prop="region" label="区域" width="140">
        <template #default="{ row }">{{ regionCn(row.region) }}</template>
      </el-table-column>
      <el-table-column prop="display_name" label="名称" min-width="160" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="stateType(row.lifecycle_state)" size="small">{{ stateText(row.lifecycle_state) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="shape" label="Shape" width="170" />
      <el-table-column prop="public_ip" label="公网 IP" width="140" />
      <el-table-column prop="private_ip" label="私网 IP" width="140" />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="success" @click="openTerminal(row)">终端</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="openChangeIp(row)">换 IP</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 卡片视图 -->
    <div v-if="viewMode === 'card'" v-loading="loading" class="inst-cards">
      <div v-for="row in pagedInstances" :key="row.instance_id" class="inst-card">
        <div class="inst-card-top">
          <div>
            <div class="inst-card-name">{{ row.display_name }}</div>
            <div class="inst-card-sub">{{ row.account_name }} · {{ regionCn(row.region) }}</div>
          </div>
          <span class="inst-dot" :class="{ on: row.lifecycle_state === 'RUNNING' }"></span>
        </div>
        <div class="inst-card-info">{{ row.public_ip || '无公网 IP' }} · {{ row.shape }}</div>
        <div class="inst-card-actions">
          <el-button size="small" type="primary" @click="openTerminal(row)">终端</el-button>
          <el-button size="small" @click="openEdit(row)">详情</el-button>
        </div>
      </div>
      <div v-if="!pagedInstances.length && !loading" style="color: #909399; font-size: 13px; padding: 24px; grid-column: 1 / -1; text-align: center">
        暂无实例
      </div>
    </div>
    <el-pagination
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      :page-sizes="[20, 50, 100]"
      :total="filteredInstances.length"
      layout="total, sizes, prev, pager, next"
      style="margin-top: 12px; justify-content: flex-end"
    />

    <Terminal ref="terminalRef" />
    <!-- 批量任务进度 -->
    <el-dialog :style="{ maxWidth: '92vw' }" v-model="progressVisible" title="批量任务进度" width="680px" :close-on-click-modal="false">
      <el-progress :percentage="progressPct" :status="task.status === 'failed' ? 'exception' : ''" />
      <p>状态：{{ statusCn(task.status) }}，成功 {{ task.success_count }} / 失败 {{ task.fail_count }} / 共 {{ task.total }}</p>
      <el-table :data="taskDetail" size="small" max-height="300">
        <el-table-column prop="display_name" label="实例" width="180" />
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '成功' : '失败' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误信息" />
      </el-table>
    </el-dialog>

    <!-- 编辑实例 -->
    <el-dialog :style="{ maxWidth: '92vw' }" v-model="editVisible" title="编辑实例" width="560px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="名称"><el-input v-model="editForm.display_name" /></el-form-item>
        <el-form-item label="OCPU">
          <el-input-number v-model="editForm.ocpus" :min="1" :max="64" :step="1"
            :disabled="!isFlex" style="width: 100%" />
          <div v-if="!isFlex" style="font-size: 12px; color: #e6a23c">当前机型 {{ editRow.shape }} 非 Flex，不支持在线调整</div>
          <div v-else style="font-size: 12px; color: #909399">仅 Flex 机型，需关机后修改</div>
        </el-form-item>
        <el-form-item label="内存 (GB)">
          <el-input-number v-model="editForm.memory_in_gbs" :min="1" :max="1024" :step="1"
            :disabled="!isFlex" style="width: 100%" />
        </el-form-item>
        <el-form-item label="硬盘 (GB)">
          <el-input-number v-model="editForm.boot_volume_size_gb" :min="50" :max="16384" :step="10"
            style="width: 100%" />
          <div style="font-size: 12px; color: #909399">只能扩容不能缩容，当前 {{ curDiskGb }} GB</div>
        </el-form-item>
        <div style="font-size: 12px; color: #909399">机型（shape）本身不支持变更，如需换机型请重建实例</div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 换 IP -->
    <el-dialog :style="{ maxWidth: '92vw' }" v-model="ipVisible" title="更换 IP（预留 IP 流程）" width="560px">
      <p>实例：{{ ipRow.display_name }}（当前 {{ ipRow.public_ip || '无公网 IP' }}）</p>
      <el-checkbox v-model="releaseOld">更换成功后释放旧的预留 IP</el-checkbox>
      <div v-if="ipResult" style="margin-top: 8px; font-size: 13px">
        <div>旧 IP：{{ ipResult.old_ip }} → 新 IP：<b>{{ ipResult.new_ip }}</b></div>
        <div v-if="ipResult.cf_synced && ipResult.cf_synced.length">
          CF 同步：
          <span v-for="s in ipResult.cf_synced" :key="s.domain" style="margin-right: 6px">
            <el-tag :type="s.ok ? 'success' : 'danger'" size="small">{{ s.domain }} {{ s.ok ? '✓' : '✗' }}</el-tag>
          </span>
        </div>
      </div>
      <template #footer>
        <el-button @click="ipVisible = false">关闭</el-button>
        <el-button type="primary" :loading="changing" @click="submitChangeIp">开始更换</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { regionCn, fmtRegion } from '../utils/regions.js'
import { useRoute } from 'vue-router'
const route = useRoute()
import { ElMessage, ElMessageBox } from 'element-plus'
import Terminal from './Terminal.vue'
import {
  listAccounts,
  listInstances,
  editInstance,
  getBootVolume,
  createBatch,
  getBatchTask,
  changeIp,
} from '../api/client.js'

const STATES = ['RUNNING', 'STOPPED', 'STOPPING', 'STARTING', 'TERMINATED', 'TERMINATING']
const STATE_TEXT = { RUNNING: '运行中', STOPPED: '已停止', STOPPING: '停止中', STARTING: '启动中', TERMINATED: '已终止', TERMINATING: '终止中' }
const stateText = (s) => STATE_TEXT[s] || s
const BATCH_TEXT = { power_on: '批量开机', power_off: '批量关机', reboot: '批量重启', terminate: '批量终止' }

const accounts = ref([])
const instances = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
// 区域选项：从实例数据构造（中文名+标识）
const regionOptions = computed(() => {
  const map = {}
  for (const i of instances.value) {
    if (i.region && !map[i.region]) {
      map[i.region] = { value: i.region, label: fmtRegion(i.region) }
    }
  }
  return Object.values(map).sort((a, b) => a.value.localeCompare(b.value))
})
const filteredInstances = computed(() => {
  let list = instances.value
  const kw = (filters.value.keyword || '').toLowerCase()
  if (kw) {
    list = list.filter(i =>
      (i.display_name || '').toLowerCase().includes(kw) ||
      (i.public_ip || '').includes(kw) ||
      (i.private_ip || '').includes(kw)
    )
  }
  return list
})
const pagedInstances = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredInstances.value.slice(start, start + pageSize.value)
})
const errors = ref([])
const selected = ref([])
const viewMode = ref('table')
const terminalRef = ref(null)
const openTerminal = (row) => {
  const accountId = row.account_id
  const instanceId = row.instance_id
  if (!accountId || !instanceId) {
    ElMessage.warning('无法确定账号或实例')
    return
  }
  terminalRef.value?.open(accountId, instanceId, row.display_name)
}
const loading = ref(false)
const filters = ref({ account_id: null, region: '', state: '', keyword: '' })

const stateType = (s) =>
  ({ RUNNING: 'success', STOPPED: '', TERMINATED: 'info', STOPPING: 'warning', STARTING: 'warning' }[s] || 'info')

const load = async (retry = true, force = false) => {
  loading.value = true
  try {
    const params = {}
    if (filters.value.account_id) params.account_id = filters.value.account_id
    if (filters.value.region) params.region = filters.value.region
    if (filters.value.state) params.state = filters.value.state
    // 手动点"刷新"时带 force=1，绕开 24 小时缓存直查 OCI
    if (force) params.force = 1
    const data = await listInstances(params)
    instances.value = data.items
    errors.value = data.errors
    // 首次加载为空时自动重试一次（OCI 首次聚合查询慢，缓存未热）
    const filterKeys = Object.keys(params).filter((k) => k !== 'force')
    if (retry && (!data.items || data.items.length === 0) && !filterKeys.length) {
      setTimeout(() => load(false, force), 3000)
    }
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ---------- 批量操作 ----------
const progressVisible = ref(false)
const task = ref({ status: '', success_count: 0, fail_count: 0, total: 0 })
const taskDetail = ref([])
let pollTimer = null

const progressPct = computed(() => {
  if (!task.value.total) return 0
  return Math.round(((task.value.success_count + task.value.fail_count) / task.value.total) * 100)
})
const statusCn = (s) => ({ done: '已完成', failed: '失败', running: '进行中' }[s] || s || '-')

let pollFailCount = 0
const pollTask = async (id) => {
  clearInterval(pollTimer)
  pollFailCount = 0
  pollTimer = setInterval(async () => {
    try {
      const t = await getBatchTask(id)
      pollFailCount = 0
      task.value = t
      taskDetail.value = t.detail?.results || []
      if (t.status === 'done' || t.status === 'failed') {
        clearInterval(pollTimer)
        load()
      }
    } catch {
      pollFailCount += 1
      if (pollFailCount >= 3) {
        clearInterval(pollTimer)
        ElMessage.error('批量任务状态查询失败，已停止轮询，请手动刷新查看结果')
      }
    }
  }, 2000)
}

const runBatch = async (type) => {
  const items = selected.value.map((r) => ({
    account_id: r.account_id,
    instance_id: r.instance_id,
    display_name: r.display_name,
  }))
  try {
    if (type === 'terminate') {
      await ElMessageBox.confirm(
        `确定要终止选中的 ${items.length} 台实例吗？此操作不可逆！`,
        '二次确认',
        { type: 'warning', confirmButtonText: '确认终止' }
      )
    } else {
      await ElMessageBox.confirm(`确定要对选中的 ${items.length} 台实例执行「${BATCH_TEXT[type]}」吗？`, '确认', {
        type: 'info',
      })
    }
  } catch {
    return
  }
  try {
    const t = await createBatch({ task_type: type, items, confirm: type === 'terminate' })
    task.value = t
    taskDetail.value = []
    progressVisible.value = true
    pollTask(t.id)
  } catch (e) {
    ElMessage.error('创建任务失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 编辑 ----------
const editVisible = ref(false)
const editing = ref(false)
const editRow = ref({})
const editForm = ref({ display_name: '' })

const isFlex = computed(() => (editRow.value.shape || '').includes('Flex'))
const curDiskGb = ref(null)
const origShape = ref({ ocpus: null, memory_in_gbs: null })

const openEdit = async (row) => {
  editRow.value = row
  const sc = row.shape_config || {}
  origShape.value = { ocpus: sc.ocpus ?? null, memory_in_gbs: sc.memoryInGBs ?? null }
  editForm.value = {
    display_name: row.display_name || '',
    ocpus: sc.ocpus ?? null,
    memory_in_gbs: sc.memoryInGBs ?? null,
    boot_volume_size_gb: null,
  }
  curDiskGb.value = null
  editVisible.value = true
  // 回填硬盘当前大小
  try {
    const bv = await getBootVolume(row.account_id, row.instance_id)
    curDiskGb.value = bv.size_gb
    editForm.value.boot_volume_size_gb = bv.size_gb
  } catch {
    // 查不到就不回填，用户手动输入目标大小
  }
}

const submitEdit = async () => {
  const body = {}
  if (editForm.value.display_name) body.display_name = editForm.value.display_name
  if (editForm.value.ocpus != null && editForm.value.ocpus !== origShape.value.ocpus)
    body.ocpus = editForm.value.ocpus
  if (editForm.value.memory_in_gbs != null && editForm.value.memory_in_gbs !== origShape.value.memory_in_gbs)
    body.memory_in_gbs = editForm.value.memory_in_gbs
  if (editForm.value.boot_volume_size_gb != null && editForm.value.boot_volume_size_gb !== curDiskGb.value)
    body.boot_volume_size_gb = editForm.value.boot_volume_size_gb
  if (!Object.keys(body).length) {
    ElMessage.warning('没有要修改的内容')
    return
  }
  editing.value = true
  try {
    await editInstance(editRow.value.account_id, editRow.value.instance_id, body)
    ElMessage.success('更新成功')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('更新失败：' + (e.response?.data?.detail || e.message))
  } finally {
    editing.value = false
  }
}

// ---------- 换 IP ----------
const ipVisible = ref(false)
const changing = ref(false)
const ipRow = ref({})
const releaseOld = ref(false)
const ipResult = ref(null)

const openChangeIp = (row) => {
  ipRow.value = row
  ipResult.value = null
  ipVisible.value = true
}

const submitChangeIp = async () => {
  changing.value = true
  try {
    const r = await changeIp({
      account_id: ipRow.value.account_id,
      instance_id: ipRow.value.instance_id,
      release_old: releaseOld.value,
    })
    ipResult.value = r
    ElMessage.success(`换 IP 成功：${r.old_ip} → ${r.new_ip}`)
    load()
  } catch (e) {
    ElMessage.error('换 IP 失败：' + (e.response?.data?.detail || e.message))
  } finally {
    changing.value = false
  }
}

onMounted(async () => {
  accounts.value = await listAccounts().catch(() => [])
  // 从账户摘要卡片跳转过来时按账号筛选
  const qid = route.query.account_id
  if (qid) filters.value.account_id = Number(qid) || qid
  load()
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
.inst-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-top: 4px;
}
.inst-card {
  background: #fff;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.inst-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
}
.inst-card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 8px;
}
.inst-card-name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
}
.inst-card-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.inst-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #d9d9d9;
  flex-shrink: 0;
  margin-top: 4px;
}
.inst-dot.on {
  background: #52c41a;
  box-shadow: 0 0 8px rgba(82, 196, 26, 0.6);
}
.inst-card-info {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inst-card-actions {
  display: flex;
  gap: 8px;
}
</style>
