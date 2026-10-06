<template>
  <div>
    <div style="margin-bottom: 12px">
      <el-button type="primary" @click="openCreate">新建抢机任务</el-button>
      <el-button @click="load" :loading="loading">刷新</el-button>
      <span style="margin-left: 8px; font-size: 12px; color: #909399">
        同一账号同一 shape 同时只允许一个进行中的任务
      </span>
    </div>

    <el-table :data="tasks" v-loading="loading" border>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="account_name" label="账号" width="120" />
      <el-table-column prop="region" label="区域" width="140" />
      <el-table-column prop="shape" label="Shape" width="180" />
      <el-table-column label="配置" width="110">
        <template #default="{ row }">{{ row.ocpus }}C / {{ row.memory_gb }}G</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="attempts" label="尝试" width="70" />
      <el-table-column prop="last_error" label="最后错误" min-width="200" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="success" v-if="row.status !== 'running'" @click="startTask(row)">启动</el-button>
          <el-button size="small" type="warning" v-if="row.status === 'running'" @click="pauseTask(row)">暂停</el-button>
          <el-button size="small" @click="openLogs(row)">日志</el-button>
          <el-button size="small" type="danger" :disabled="row.status === 'running'" @click="delTask(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建任务 -->
    <el-dialog v-model="createVisible" title="新建抢机任务" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="场景模板">
          <el-select v-model="templateIdx" placeholder="选择后一键填入" clearable style="width: 100%" @change="applyTemplate">
            <el-option v-for="(t, i) in templates" :key="i" :value="i" :label="t.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号" required>
          <el-select v-model="form.account_id" placeholder="选择账号" style="width: 100%" @change="onAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}（${a.region}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="区域" required>
          <el-input v-model="form.region" placeholder="如 ap-seoul-1" @change="clearOciOptions" />
        </el-form-item>
        <el-form-item label="Shape" required><el-input v-model="form.shape" placeholder="VM.Standard.A1.Flex" /></el-form-item>
        <el-form-item label="OCPU / 内存">
          <el-input-number v-model="form.ocpus" :min="1" :step="1" style="width: 130px" />
          <span style="margin: 0 8px">/</span>
          <el-input-number v-model="form.memory_gb" :min="1" :step="1" style="width: 130px" />
          <span style="margin-left: 4px">GB</span>
        </el-form-item>
        <el-form-item label="镜像 OCID" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-if="imageOptions.length" v-model="form.image_ocid" filterable allow-create
              placeholder="选择或手动输入镜像 OCID" style="flex: 1">
              <el-option v-for="o in imageOptions" :key="o.ocid" :value="o.ocid"
                :label="`${o.display_name}（${o.operating_system}）`" />
            </el-select>
            <el-input v-else v-model="form.image_ocid" placeholder="ocid1.image.oc1...." style="flex: 1" />
            <el-button :loading="fetching.image" @click="fetchImages">获取</el-button>
          </div>
        </el-form-item>
        <el-form-item label="子网 OCID" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-if="subnetOptions.length" v-model="form.subnet_ocid" filterable allow-create
              placeholder="选择或手动输入子网 OCID" style="flex: 1">
              <el-option v-for="o in subnetOptions" :key="o.ocid" :value="o.ocid"
                :label="`${o.display_name}（${o.vcn_name} ${o.cidr}）`" />
            </el-select>
            <el-input v-else v-model="form.subnet_ocid" placeholder="ocid1.subnet.oc1...." style="flex: 1" />
            <el-button :loading="fetching.subnet" @click="fetchSubnets">获取</el-button>
          </div>
        </el-form-item>
        <el-form-item label="可用域" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-if="adOptions.length" v-model="form.availability_domain" filterable allow-create
              placeholder="选择或手动输入可用域" style="flex: 1">
              <el-option v-for="o in adOptions" :key="o.name" :value="o.name" :label="o.name" />
            </el-select>
            <el-input v-else v-model="form.availability_domain" placeholder="如 Uocm:AP-SEOUL-1-AD-1" style="flex: 1" />
            <el-button :loading="fetching.ad" @click="fetchAds">获取</el-button>
          </div>
        </el-form-item>
        <el-form-item label="实例显示名"><el-input v-model="form.display_name" placeholder="空则自动生成 snipe-{id}-时间" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 实时日志 -->
    <el-drawer v-model="logsVisible" :title="`任务 #${logTask?.id} 实时日志`" size="640px" @closed="stopLogPoll">
      <div style="margin-bottom: 8px">
        <el-select v-model="logLevel" clearable placeholder="全部级别" style="width: 140px" @change="loadLogs">
          <el-option value="info" label="info" />
          <el-option value="warning" label="warning" />
          <el-option value="error" label="error" />
        </el-select>
        <el-button style="margin-left: 8px" @click="loadLogs">刷新</el-button>
        <span style="margin-left: 8px; font-size: 12px; color: #909399">每 3 秒自动刷新</span>
      </div>
      <div style="background: #1e1e1e; color: #d4d4d4; border-radius: 4px; padding: 12px; height: 70vh; overflow-y: auto; font-family: monospace; font-size: 12px">
        <div v-for="l in logs" :key="l.id" style="margin-bottom: 4px">
          <span style="color: #858585">{{ l.created_at }}</span>
          <span :style="{ color: levelColor(l.level), margin: '0 6px' }">[{{ l.level }}]</span>
          <span>{{ l.message }}</span>
        </div>
        <div v-if="!logs.length" style="color: #858585">暂无日志</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  listSnipeTasks,
  createSnipeTask,
  startSnipeTask,
  pauseSnipeTask,
  deleteSnipeTask,
  getSnipeLogs,
  getSnipeTemplates,
  getOciAvailabilityDomains,
  getOciImages,
  getOciSubnets,
} from '../api/client'

const STATUS_MAP = {
  pending: '待启动', running: '抢机中', paused: '已暂停',
  success: '已抢到', stopped: '已停止', failed: '失败',
}
const statusText = (s) => STATUS_MAP[s] || s
const statusType = (s) =>
  ({ running: 'primary', success: 'success', failed: 'danger', paused: 'warning' }[s] || 'info')
const levelColor = (l) => ({ info: '#9cdcfe', warning: '#dcdcaa', error: '#f48771' }[l] || '#d4d4d4')

const tasks = ref([])
const accounts = ref([])
const templates = ref([])
const loading = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const templateIdx = ref(null)
const form = ref({
  account_id: null, region: '', shape: 'VM.Standard.A1.Flex',
  ocpus: 4, memory_gb: 24, image_ocid: '', subnet_ocid: '',
  availability_domain: '', display_name: '',
})

// OCI 级联选项：点"获取"后从 OCI 实时查询填充
const adOptions = ref([])
const imageOptions = ref([])
const subnetOptions = ref([])
const fetching = ref({ ad: false, image: false, subnet: false })

const clearOciOptions = () => {
  adOptions.value = []
  imageOptions.value = []
  subnetOptions.value = []
}

// 选账号后自动带出该账号的默认区域，之前拉取的 OCI 选项失效清空
const onAccountChange = () => {
  const a = accounts.value.find((x) => x.id === form.value.account_id)
  if (a && a.region) form.value.region = a.region
  clearOciOptions()
}

const needAccountRegion = () => {
  if (!form.value.account_id) {
    ElMessage.warning('请先选择账号')
    return null
  }
  if (!form.value.region) {
    ElMessage.warning('请先填写区域')
    return null
  }
  return { account_id: form.value.account_id, region: form.value.region }
}

const errDetail = (e) => e.response?.data?.detail || e.message

const fetchAds = async () => {
  const p = needAccountRegion()
  if (!p) return
  fetching.value.ad = true
  try {
    adOptions.value = await getOciAvailabilityDomains(p)
    if (!adOptions.value.length) ElMessage.warning('该区域未返回可用域')
  } catch (e) {
    ElMessage.error('获取可用域失败：' + errDetail(e))
  } finally {
    fetching.value.ad = false
  }
}

const fetchImages = async () => {
  const p = needAccountRegion()
  if (!p) return
  fetching.value.image = true
  try {
    imageOptions.value = await getOciImages(p)
    if (!imageOptions.value.length) ElMessage.warning('该区域未返回平台镜像')
  } catch (e) {
    ElMessage.error('获取镜像失败：' + errDetail(e))
  } finally {
    fetching.value.image = false
  }
}

const fetchSubnets = async () => {
  const p = needAccountRegion()
  if (!p) return
  fetching.value.subnet = true
  try {
    subnetOptions.value = await getOciSubnets(p)
    if (!subnetOptions.value.length) ElMessage.warning('该 compartment 下未找到子网')
  } catch (e) {
    ElMessage.error('获取子网失败：' + errDetail(e))
  } finally {
    fetching.value.subnet = false
  }
}

const logsVisible = ref(false)
const logTask = ref(null)
const logs = ref([])
const logLevel = ref('')
let logTimer = null

const load = async () => {
  loading.value = true
  try {
    tasks.value = await listSnipeTasks()
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const openCreate = async () => {
  templateIdx.value = null
  Object.assign(form.value, {
    account_id: null, region: '', shape: 'VM.Standard.A1.Flex',
    ocpus: 4, memory_gb: 24, image_ocid: '', subnet_ocid: '',
    availability_domain: '', display_name: '',
  })
  clearOciOptions()
  try {
    accounts.value = await listAccounts()
    templates.value = await getSnipeTemplates()
  } catch (e) {
    ElMessage.error('加载账号/模板失败：' + (e.response?.data?.detail || e.message))
  }
  createVisible.value = true
}

const applyTemplate = () => {
  const t = templates.value[templateIdx.value]
  if (!t) return
  // 模板只含 shape 配置，不碰区域（区域跟随所选账号）
  form.value.shape = t.shape
  form.value.ocpus = t.ocpus
  form.value.memory_gb = t.memory_gb
}

const submitCreate = async () => {
  if (!form.value.account_id || !form.value.region || !form.value.shape ||
      !form.value.image_ocid || !form.value.subnet_ocid || !form.value.availability_domain) {
    ElMessage.warning('请填写必填项')
    return
  }
  creating.value = true
  try {
    await createSnipeTask(form.value)
    ElMessage.success('任务已创建（待启动）')
    createVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

const startTask = async (row) => {
  try {
    await startSnipeTask(row.id)
    ElMessage.success('任务已启动')
    load()
  } catch (e) {
    ElMessage.error('启动失败：' + (e.response?.data?.detail || e.message))
  }
}

const pauseTask = async (row) => {
  try {
    await pauseSnipeTask(row.id)
    ElMessage.success('任务已暂停')
    load()
  } catch (e) {
    ElMessage.error('暂停失败：' + (e.response?.data?.detail || e.message))
  }
}

const delTask = async (row) => {
  try {
    await ElMessageBox.confirm(`删除抢机任务 #${row.id}？日志将一并删除。`, '确认', { type: 'warning' })
    await deleteSnipeTask(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

const loadLogs = async () => {
  if (!logTask.value) return
  try {
    const r = await getSnipeLogs(logTask.value.id, { level: logLevel.value || undefined, size: 200 })
    logs.value = r.items
  } catch (e) {
    ElMessage.error('日志加载失败：' + (e.response?.data?.detail || e.message))
  }
}

const openLogs = (row) => {
  logTask.value = row
  logLevel.value = ''
  logsVisible.value = true
  loadLogs()
  stopLogPoll()
  logTimer = setInterval(loadLogs, 3000)
}

const stopLogPoll = () => {
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
}

onMounted(load)
onUnmounted(stopLogPoll)
</script>
