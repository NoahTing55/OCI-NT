<template>
  <div>
    <el-form :inline="true" :model="filters">
      <el-form-item label="账号">
        <el-select v-model="filters.account_id" clearable placeholder="全部" style="width: 160px">
          <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="区域">
        <el-input v-model="filters.region" clearable placeholder="如 ap-seoul-1" style="width: 160px" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="filters.state" clearable placeholder="全部" style="width: 150px">
          <el-option v-for="s in STATES" :key="s" :value="s" :label="s" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="load" :loading="loading">刷新</el-button>
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

    <div style="margin: 8px 0">
      <el-button size="small" type="success" :disabled="!selected.length" @click="runBatch('power_on')">批量开机</el-button>
      <el-button size="small" :disabled="!selected.length" @click="runBatch('power_off')">批量关机</el-button>
      <el-button size="small" :disabled="!selected.length" @click="runBatch('reboot')">批量重启</el-button>
      <el-button size="small" type="danger" :disabled="!selected.length" @click="runBatch('terminate')">批量终止</el-button>
      <span style="margin-left: 8px; color: #909399; font-size: 12px">已选 {{ selected.length }} 台</span>
    </div>

    <el-table :data="instances" v-loading="loading" border @selection-change="selected = $event">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="account_name" label="账号" width="120" />
      <el-table-column prop="region" label="区域" width="140" />
      <el-table-column prop="display_name" label="名称" width="160" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="stateType(row.lifecycle_state)" size="small">{{ row.lifecycle_state }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="shape" label="Shape" width="170" />
      <el-table-column prop="public_ip" label="公网 IP" width="140" />
      <el-table-column prop="private_ip" label="私网 IP" width="140" />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="openChangeIp(row)">换 IP</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 批量任务进度 -->
    <el-dialog v-model="progressVisible" title="批量任务进度" width="680px" :close-on-click-modal="false">
      <el-progress :percentage="progressPct" :status="task.status === 'failed' ? 'exception' : ''" />
      <p>状态：{{ task.status }}，成功 {{ task.success_count }} / 失败 {{ task.fail_count }} / 共 {{ task.total }}</p>
      <el-table :data="taskDetail" size="small" max-height="300" border>
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
    <el-dialog v-model="editVisible" title="编辑实例" width="520px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="名称"><el-input v-model="editForm.display_name" /></el-form-item>
        <el-form-item label="freeform_tags">
          <el-input v-model="tagsText" type="textarea" :rows="3" placeholder='{"env":"prod"}' />
        </el-form-item>
        <el-form-item label="metadata">
          <el-input v-model="metaText" type="textarea" :rows="3" placeholder='{"user_data":"..."}' />
        </el-form-item>
        <div style="font-size: 12px; color: #909399">shape / OCPU / 内存不支持在线变更，如需升配请重建实例</div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 换 IP -->
    <el-dialog v-model="ipVisible" title="更换 IP（预留 IP 流程）" width="460px">
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
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  listInstances,
  editInstance,
  createBatch,
  getBatchTask,
  changeIp,
} from '../api/client.js'

const STATES = ['RUNNING', 'STOPPED', 'STOPPING', 'STARTING', 'TERMINATED', 'TERMINATING']
const BATCH_TEXT = { power_on: '批量开机', power_off: '批量关机', reboot: '批量重启', terminate: '批量终止' }

const accounts = ref([])
const instances = ref([])
const errors = ref([])
const selected = ref([])
const loading = ref(false)
const filters = ref({ account_id: null, region: '', state: '' })

const stateType = (s) =>
  ({ RUNNING: 'success', STOPPED: '', TERMINATED: 'info', STOPPING: 'warning', STARTING: 'warning' }[s] || 'info')

const load = async () => {
  loading.value = true
  try {
    const params = {}
    if (filters.value.account_id) params.account_id = filters.value.account_id
    if (filters.value.region) params.region = filters.value.region
    if (filters.value.state) params.state = filters.value.state
    const data = await listInstances(params)
    instances.value = data.items
    errors.value = data.errors
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

const pollTask = async (id) => {
  clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const t = await getBatchTask(id)
      task.value = t
      taskDetail.value = t.detail?.results || []
      if (t.status === 'done' || t.status === 'failed') {
        clearInterval(pollTimer)
        load()
      }
    } catch {
      clearInterval(pollTimer)
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
const tagsText = ref('')
const metaText = ref('')

const openEdit = (row) => {
  editRow.value = row
  editForm.value = { display_name: row.display_name || '' }
  tagsText.value = ''
  metaText.value = ''
  editVisible.value = true
}

const submitEdit = async () => {
  const body = {}
  if (editForm.value.display_name) body.display_name = editForm.value.display_name
  for (const [key, text] of [['freeform_tags', tagsText], ['metadata', metaText]]) {
    if (text.value.trim()) {
      try {
        body[key] = JSON.parse(text.value)
      } catch {
        ElMessage.error(`${key} 不是合法 JSON`)
        return
      }
    }
  }
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
const releaseOld = ref(true)
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
  const qid = useRoute().query.account_id
  if (qid) filters.value.account_id = Number(qid) || qid
  load()
})
onUnmounted(() => clearInterval(pollTimer))
</script>
