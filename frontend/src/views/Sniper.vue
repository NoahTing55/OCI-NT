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
        <!-- 场景模板：OCI-Start 式卡片选择，点击填入 shape/OCPU/内存，可再手动调整 -->
        <el-form-item label="场景模板">
          <div style="width: 100%">
            <div class="tpl-cards">
              <div v-for="(t, i) in templates" :key="i"
                class="tpl-card" :class="{ active: templateIdx === i }"
                @click="selectTemplate(i)">
                <div class="tpl-card-name">{{ t.name }}</div>
                <div class="tpl-card-desc">{{ t.shape }}</div>
                <div class="tpl-card-desc">{{ t.ocpus }}C / {{ t.memory_gb }}G · {{ t.shape.includes('A1') ? 'ARM' : 'AMD' }} 架构</div>
              </div>
            </div>
            <div style="color: #909399; font-size: 12px; margin-top: 4px">点击卡片填入配置，可再手动调整</div>
          </div>
        </el-form-item>
        <el-form-item label="账号" required>
          <el-select v-model="form.account_id" placeholder="选择账号" style="width: 100%" @change="onAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}（${a.region}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="区域" required>
          <el-input v-model="form.region" placeholder="如 ap-seoul-1" @change="onRegionChange" />
        </el-form-item>
        <el-form-item label="Compartment">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-if="compOptions.length" v-model="form.compartment_ocid" filterable allow-create
              placeholder="选择或手动输入 Compartment OCID" style="flex: 1" @change="subnetOptions = []">
              <el-option v-for="o in compOptions" :key="o.ocid" :value="o.ocid" :label="o.name" />
            </el-select>
            <el-input v-else v-model="form.compartment_ocid" placeholder="空则用账号根 compartment" style="flex: 1" />
            <el-button :loading="fetching.comp" @click="fetchComps">获取</el-button>
          </div>
        </el-form-item>
        <el-form-item label="Shape" required><el-input v-model="form.shape" placeholder="VM.Standard.A1.Flex" /></el-form-item>
        <el-form-item label="OCPU / 内存">
          <el-input-number v-model="form.ocpus" :min="1" :step="1" style="width: 130px" />
          <span style="margin: 0 8px">/</span>
          <el-input-number v-model="form.memory_gb" :min="1" :step="1" style="width: 130px" />
          <span style="margin-left: 4px">GB</span>
        </el-form-item>
        <el-form-item label="镜像" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-model="imageOs" placeholder="操作系统" style="flex: 1"
              :loading="fetching.image" @change="onImageOsChange">
              <el-option v-for="os in imageOsList" :key="os" :value="os" :label="os" />
            </el-select>
            <el-select v-model="form.image_ocid" filterable allow-create
              placeholder="系统版本（可直接粘贴 OCID）" style="flex: 1">
              <el-option v-for="v in imageVersionList" :key="v.ocid" :value="v.ocid"
                :label="v.operating_system_version" />
            </el-select>
          </div>
          <template #extra><span style="color:#909399;font-size:12px">按 shape 架构自动拉取：先选操作系统，再选版本</span></template>
        </el-form-item>
        <el-form-item label="子网 OCID">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select v-if="subnetOptions.length" v-model="form.subnet_ocid" filterable allow-create
              placeholder="选择或手动输入子网 OCID" style="flex: 1">
              <el-option v-for="o in subnetOptions" :key="o.ocid" :value="o.ocid"
                :label="`[${o.compartment_name || '未知'}] ${o.display_name}（${o.vcn_name} ${o.cidr}）`" />
            </el-select>
            <el-input v-else v-model="form.subnet_ocid" placeholder="ocid1.subnet.oc1...." style="flex: 1" />
            <el-button :loading="fetching.subnet" @click="fetchSubnets">获取</el-button>
          </div>
          <template #extra><span style="color:#909399;font-size:12px">留空则任务启动时自动创建网络（有则复用、无则创建）</span></template>
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
        <el-form-item label="Root 密码">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-input v-model="form.root_password" placeholder="留空则自动生成随机密码" style="flex: 1" show-password />
            <el-button @click="form.root_password = randomPassword()">随机</el-button>
          </div>
          <template #extra><span style="color:#909399;font-size:12px">通过 cloud-init 在开机时设置，TG 通知会带上</span></template>
        </el-form-item>
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
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
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
  getOciCompartments,
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
  availability_domain: '', display_name: '', compartment_ocid: '', root_password: '',
})

// 随机密码：去掉易混淆字符（0/O、1/l/I），12 位
const randomPassword = () => {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  const arr = new Uint32Array(12)
  crypto.getRandomValues(arr)
  return Array.from(arr, (x) => chars[x % chars.length]).join('')
}

// OCI 级联选项：点"获取"后从 OCI 实时查询填充；镜像为「操作系统→版本」两级下拉
const adOptions = ref([])
const subnetOptions = ref([])
const compOptions = ref([])
const fetching = ref({ ad: false, image: false, subnet: false, comp: false })

// 镜像两级下拉状态
const imageOs = ref('')
const imageOsList = ref([])
const imageVersionMap = ref({})  // os -> [{ocid, operating_system_version, ...}]
const imageVersionList = computed(() => imageVersionMap.value[imageOs.value] || [])

// shape 决定架构：A1 → ARM，E2/E5/E4 → AMD（OCI-Start 按模板卡选架构的思路）
const shapeArch = computed(() => {
  const s = (form.value.shape || '').toUpperCase()
  if (s.includes('A1')) return 'arm'
  if (s.includes('E2') || s.includes('E5') || s.includes('E4')) return 'amd'
  return ''
})

const clearOciOptions = () => {
  adOptions.value = []
  subnetOptions.value = []
  compOptions.value = []
  imageOs.value = ''
  imageOsList.value = []
  imageVersionMap.value = {}
}

// 区域手改后选项失效清空，镜像自动重拉
const onRegionChange = () => {
  clearOciOptions()
  fetchImages()
}

// 选账号后自动带出该账号的默认区域，之前拉取的 OCI 选项失效清空，镜像自动重拉
const onAccountChange = () => {
  const a = accounts.value.find((x) => x.id === form.value.account_id)
  if (a && a.region) form.value.region = a.region
  clearOciOptions()
  fetchImages()
  fetchComps()  // 选账号后自动加载 compartment 列表，不用手动点获取
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
  const arch = shapeArch.value
  if (!p || !arch) {
    imageOs.value = ''
    imageOsList.value = []
    imageVersionMap.value = {}
    return
  }
  fetching.value.image = true
  try {
    const list = await getOciImages({ ...p, arch })
    // 按操作系统分组
    const map = {}
    for (const it of list) {
      const os = it.operating_system || '其他'
      ;(map[os] = map[os] || []).push(it)
    }
    imageOsList.value = Object.keys(map)
    imageVersionMap.value = map
    if (imageOsList.value.length) {
      // 自动选中第一个 OS 和第一个版本，imageId 自动填入
      imageOs.value = imageOsList.value[0]
      onImageOsChange()
    } else {
      imageOs.value = ''
      ElMessage.warning('该区域未返回该架构的平台镜像')
    }
  } catch (e) {
    ElMessage.error('获取镜像失败：' + errDetail(e))
  } finally {
    fetching.value.image = false
  }
}

// 切换操作系统后自动选中该 OS 的第一个版本
const onImageOsChange = () => {
  const vers = imageVersionMap.value[imageOs.value] || []
  form.value.image_ocid = vers.length ? vers[0].ocid : ''
}

// shape 变化导致架构变化时重拉镜像（如模板切换、手改 shape）
watch(shapeArch, () => {
  if (form.value.account_id) fetchImages()
})

const fetchSubnets = async () => {
  const p = needAccountRegion()
  if (!p) return
  fetching.value.subnet = true
  try {
    // 不传 compartment：后端自动搜整个 tenancy 树，一键找出所有子网
    subnetOptions.value = await getOciSubnets({ ...p })
    if (!subnetOptions.value.length) ElMessage.warning('整个 tenancy 下都未找到子网，请先在 OCI 控制台创建 VCN/子网')
  } catch (e) {
    ElMessage.error('获取子网失败：' + errDetail(e))
  } finally {
    fetching.value.subnet = false
  }
}

const fetchComps = async () => {
  const p = needAccountRegion()
  if (!p) return
  fetching.value.comp = true
  try {
    compOptions.value = await getOciCompartments(p)
    if (!compOptions.value.length) {
      ElMessage.warning('未返回 compartment')
    } else if (!form.value.compartment_ocid) {
      // 默认选中根 tenancy
      form.value.compartment_ocid = compOptions.value[0].ocid
    }
  } catch (e) {
    ElMessage.error('获取 Compartment 失败：' + errDetail(e))
  } finally {
    fetching.value.comp = false
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
    availability_domain: '', display_name: '', compartment_ocid: '', root_password: '',
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

// 卡片点击：选中并填入模板（shape 变化会触发 shapeArch watcher 自动刷新操作系统列表）
const selectTemplate = (i) => {
  templateIdx.value = i
  applyTemplate()
}

const submitCreate = async () => {
  if (!form.value.account_id || !form.value.region || !form.value.shape ||
      !form.value.image_ocid || !form.value.availability_domain) {
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

<style scoped>
/* 场景模板卡片：OCI-Start 式可视化选择 */
.tpl-cards {
  display: flex;
  gap: 12px;
}
.tpl-card {
  flex: 1;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 12px 14px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  background: #fff;
}
.tpl-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}
.tpl-card.active {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px var(--el-color-primary);
}
.tpl-card-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 6px;
}
.tpl-card.active .tpl-card-name {
  color: var(--el-color-primary);
}
.tpl-card-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
</style>
