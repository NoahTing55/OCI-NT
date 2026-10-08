<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建实例</el-button>
      <el-button @click="load" :loading="loading">刷新</el-button>
      <span class="toolbar-tip">同一账号同一 shape 同时只允许一个进行中的任务</span>
    </div>

    <el-table :data="tasks" v-loading="loading" border>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="account_name" label="账号" width="120" />
      <el-table-column prop="region" label="区域" width="140" />
      <el-table-column prop="shape" label="Shape" width="180" />
      <el-table-column label="配置" width="130">
        <template #default="{ row }">{{ row.ocpus }}C / {{ row.memory_gb }}G<span v-if="(row.target_count || 1) > 1"> ×{{ row.target_count }} 台</span></template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="attempts" label="尝试" width="70" />
      <el-table-column prop="last_error" label="最后错误" min-width="200" show-overflow-tooltip />
      <el-table-column label="创建时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <div class="op-btns">
            <el-button size="small" type="success" v-if="!['running', 'success'].includes(row.status)" @click="startTask(row)">启动</el-button>
            <el-button size="small" type="warning" v-if="row.status === 'running'" @click="pauseTask(row)">暂停</el-button>
            <el-tooltip :content="row.status === 'running' ? '任务运行中，不可编辑' : row.status === 'success' ? '任务已完成，不可编辑' : '编辑任务'" placement="top">
              <el-button size="small" :disabled="['running', 'success'].includes(row.status)" @click="openEdit(row)">编辑</el-button>
            </el-tooltip>
            <el-button size="small" @click="openLogs(row)">日志</el-button>
            <el-button size="small" v-if="row.root_password" @click="showPassword(row)">密码</el-button>
            <el-button size="small" type="danger" :disabled="row.status === 'running'" @click="delTask(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建任务：分组布局（模板 → 基础 → 实例 → 高级折叠） -->
    <el-dialog v-model="createVisible" :title="isEdit ? '编辑实例任务' : '新建实例'" width="640px">
      <el-form :model="form" label-width="100px">
        <!-- 分组1：选择模板（2×2 卡片网格） -->
        <div class="form-group-title">选择模板</div>
        <div class="tpl-cards tpl-cards-4">
          <div v-for="(t, i) in templates" :key="i"
            class="tpl-card" :class="{ active: templateIdx === i }"
            @click="selectTemplate(i)">
            <div class="tpl-card-name">{{ t.name }}
              <el-tag size="small" :type="archTagType(t.shape)" style="margin-left: 6px">{{ archLabel(t.shape) }}</el-tag>
            </div>
            <div class="tpl-card-desc">{{ t.shape }}</div>
            <div class="tpl-card-desc">{{ t.ocpus }}C / {{ t.memory_gb }}G</div>
          </div>
        </div>
        <div style="color: #909399; font-size: 12px; margin: 4px 0 12px">点击卡片填入配置，可再手动调整</div>

        <!-- 分组2：基础配置 -->
        <div class="form-group-title">基础配置</div>
        <el-form-item label="账号" required>
          <!-- 新建模式：多选账号，支持批量创建任务 -->
          <el-select v-if="!isEdit" v-model="selectedAccountIds" multiple collapse-tags collapse-tags-tooltip
            placeholder="选择账号（可多选，批量创建）" style="width: 100%" @change="onAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}（${a.region}）`" />
          </el-select>
          <!-- 编辑模式：单选 -->
          <el-select v-else v-model="form.account_id" placeholder="选择账号" style="width: 100%" @change="onAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}（${a.region}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="区域" required>
          <el-select v-model="form.region" placeholder="如 ap-seoul-1" filterable allow-create
            style="width: 100%" @change="onRegionChange">
            <el-option v-for="r in regionOptions" :key="r" :value="r" :label="fmtRegion(r)" />
          </el-select>
        </el-form-item>
        <el-form-item label="开机数量">
          <el-input-number v-model="form.target_count" :min="1" :max="100" style="width: 160px" />
          <span style="color: #909399; font-size: 12px; margin-left: 8px">同一账号连续抢 N 台（每台实例名自动加序号）</span>
        </el-form-item>
        <el-form-item label="开机间隔">
          <el-button-group class="interval-btns" style="margin-right: 8px">
            <el-button size="small" :type="form.interval_seconds === 30 ? 'primary' : ''" @click="form.interval_seconds = 30">30s</el-button>
            <el-button size="small" :type="form.interval_seconds === 60 ? 'primary' : ''" @click="form.interval_seconds = 60">60s</el-button>
            <el-button size="small" :type="form.interval_seconds === 300 ? 'primary' : ''" @click="form.interval_seconds = 300">300s</el-button>
          </el-button-group>
          <el-input-number v-model="form.interval_seconds" :min="5" :max="3600" style="width: 130px" />
          <span style="color: #909399; font-size: 12px; margin-left: 8px">秒（无可用容量时的重试间隔）</span>
        </el-form-item>

        <!-- 分组3：实例配置 -->
        <div class="form-group-title">实例配置</div>
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
        <el-form-item label="硬盘容量">
          <el-input-number v-model="form.boot_volume_gb" :min="50" :max="16384" :step="10" style="width: 130px" />
          <span style="margin-left: 4px">GB</span>
          <span style="color: #909399; font-size: 12px; margin-left: 8px">启动卷大小（50-16384）</span>
        </el-form-item>
        <el-form-item label="放行所有端口">
          <el-switch v-model="form.open_all_ports" />
          <span style="color: #909399; font-size: 12px; margin-left: 8px">开机后自动在安全列表添加全端口放行规则</span>
        </el-form-item>

        <!-- 分组4：高级（默认折叠） -->
        <el-collapse style="margin-top: 8px">
          <el-collapse-item title="高级选项（Compartment / 子网 / 显示名 / Root 密码）" name="advanced">
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
            <el-form-item label="实例显示名"><el-input v-model="form.display_name" placeholder="空则自动生成 Oracle-{id}-时间（多台自动加序号）" /></el-form-item>
            <el-form-item label="Root 密码">
              <div style="display: flex; gap: 8px; width: 100%">
                <el-input v-model="form.root_password" placeholder="留空则自动生成随机密码" style="flex: 1" show-password />
                <el-button @click="form.root_password = randomPassword()">随机</el-button>
              </div>
              <template #extra><span style="color:#909399;font-size:12px">通过 cloud-init 在开机时设置，TG 通知会带上</span></template>
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">{{ isEdit ? '保存' : '创建' }}</el-button>
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

    <!-- Root 密码查看 -->
    <el-dialog v-model="pwdVisible" title="Root 密码" width="420px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="任务">#{{ pwdTask?.id }}（{{ pwdTask?.account_name }}）</el-descriptions-item>
        <el-descriptions-item label="用户">root</el-descriptions-item>
        <el-descriptions-item label="密码">
          <el-input :value="pwdTask?.root_password" readonly show-password style="width: 220px" @click="selectPwd" />
          <el-button size="small" @click="copyPwd" style="margin-left: 8px">复制</el-button>
        </el-descriptions-item>
      </el-descriptions>
      <div style="color: #909399; font-size: 12px; margin-top: 12px">密码通过 cloud-init 注入，新机器首次启动后约 1-2 分钟生效</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  listSnipeTasks,
  createSnipeTask,
  batchCreateSnipeTasks,
  updateSnipeTask,
  startSnipeTask,
  pauseSnipeTask,
  deleteSnipeTask,
  getSnipeLogs,
  getSnipeTemplates,
  getOciAvailabilityDomains,
  getOciImages,
  getOciSubnets,
  getOciCompartments,
  listRegionSubscriptions,
} from '../api/client'

const STATUS_MAP = {
  pending: '待启动', running: '运行中', paused: '已暂停',
  success: '已完成', stopped: '已停止', failed: '失败',
}
const statusText = (s) => STATUS_MAP[s] || s
// 格式化时间：2026-10-07T05:24:25 → 2026-10-07 05:24
const fmtTime = (v) => {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 16)
}
const statusType = (s) =>
  ({ running: 'primary', success: 'success', failed: 'danger', paused: 'warning' }[s] || 'info')
const levelColor = (l) => ({ info: '#9cdcfe', warning: '#dcdcaa', error: '#f48771' }[l] || '#d4d4d4')
// 模板卡片架构标签：A1→ARM，E2→AMD，E5→x86
const archLabel = (shape) => shape.includes('A1') ? 'ARM' : (shape.includes('E5') ? 'x86' : 'AMD')
const archTagType = (shape) => shape.includes('A1') ? 'success' : (shape.includes('E5') ? 'warning' : 'info')

const tasks = ref([])
// 区域中文名映射
const REGION_CN = {
  "us-phoenix-1": "凤凰城", "us-ashburn-1": "阿什本", "us-sanjose-1": "圣何塞", "us-chicago-1": "芝加哥",
  "ap-singapore-1": "新加坡", "ap-singapore-2": "新加坡西", "ap-tokyo-1": "东京", "ap-osaka-1": "大阪",
  "ap-seoul-1": "首尔", "ap-chuncheon-1": "春川", "ap-mumbai-1": "孟买", "ap-hyderabad-1": "海得拉巴",
  "ap-sydney-1": "悉尼", "ap-melbourne-1": "墨尔本", "ap-batam-1": "巴淡岛", "ap-kulai-2": "古来",
  "eu-frankfurt-1": "法兰克福", "eu-paris-1": "巴黎", "eu-marseille-1": "马赛", "eu-milan-1": "米兰",
  "eu-turin-1": "都灵", "eu-amsterdam-1": "阿姆斯特丹", "eu-madrid-1": "马德里", "eu-madrid-3": "马德里西",
  "eu-stockholm-1": "斯德哥尔摩", "eu-zurich-1": "苏黎世", "eu-jovanovac-1": "约瓦诺瓦茨",
  "uk-london-1": "伦敦", "uk-cardiff-1": "卡迪夫",
  "ca-toronto-1": "多伦多", "ca-montreal-1": "蒙特利尔",
  "sa-saopaulo-1": "圣保罗", "sa-vinhedo-1": "维涅杜", "sa-santiago-1": "圣地亚哥",
  "sa-valparaiso-1": "瓦尔帕莱索", "sa-bogota-1": "波哥大",
  "me-dubai-1": "迪拜", "me-abudhabi-1": "阿布扎比", "me-jeddah-1": "吉达", "me-riyadh-1": "利雅得",
  "il-jerusalem-1": "耶路撒冷",
  "mx-monterrey-1": "蒙特雷", "mx-queretaro-1": "克雷塔罗",
  "af-johannesburg-1": "约翰内斯堡", "af-casablanca-1": "卡萨布兰卡",
}
const fmtRegion = (r) => r ? `${r} ${REGION_CN[r] || ''}`.trim() : '-'
const accounts = ref([])
const templates = ref([])
const loading = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const isEdit = ref(false)
const editingId = ref(null)
const templateIdx = ref(null)
// 批量创建：新建模式下多选的账号 ID 列表
const selectedAccountIds = ref([])
const form = ref({
  account_id: null, region: '', shape: 'VM.Standard.A1.Flex',
  ocpus: 4, memory_gb: 24, image_ocid: '', subnet_ocid: '',
  availability_domain: '', display_name: '', compartment_ocid: '', root_password: '',
  target_count: 1, interval_seconds: 60, open_all_ports: true, boot_volume_gb: 50,
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

// 区域下拉回退列表（订阅接口失败时用）
const FALLBACK_REGIONS = ['ap-seoul-1', 'ap-tokyo-1', 'ap-singapore-1', 'ap-osaka-1', 'us-phoenix-1', 'us-ashburn-1', 'eu-frankfurt-1']
const regionOptions = ref([...FALLBACK_REGIONS])

// 拉取账号的已订阅区域：成功用订阅列表；失败时（免费号 404）只用该账号主区域；
// 无账号时才回退硬编码列表
const loadRegionOptions = async (accountId) => {
  const a = accounts.value.find((x) => x.id === accountId)
  if (!accountId || !a) {
    regionOptions.value = [...FALLBACK_REGIONS]
    return
  }
  try {
    const subs = await listRegionSubscriptions(accountId)
    const names = (subs || []).map((s) => s.region_name).filter(Boolean)
    if (names.length) regionOptions.value = names
    else regionOptions.value = a.region ? [a.region] : [...FALLBACK_REGIONS]
  } catch {
    // 免费账户调订阅接口 404，只显示主区域
    regionOptions.value = a.region ? [a.region] : [...FALLBACK_REGIONS]
  }
}

// 选账号后自动带出该账号的默认区域，之前拉取的 OCI 选项失效清空，镜像自动重拉
// 批量模式下用首选账号加载选项（区域/镜像/可用域等）
const onAccountChange = () => {
  // 新建多选模式：form.account_id 取首选账号，供下方选项加载逻辑使用
  if (!isEdit.value) {
    form.value.account_id = selectedAccountIds.value[0] ?? null
  }
  const a = accounts.value.find((x) => x.id === form.value.account_id)
  if (a && a.region) form.value.region = a.region
  loadRegionOptions(form.value.account_id)
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
const pwdVisible = ref(false)
const pwdTask = ref(null)
const showPassword = (row) => { pwdTask.value = row; pwdVisible.value = true }
const copyPwd = async () => {
  const text = pwdTask.value?.root_password || ''
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // HTTP 非安全上下文降级方案
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success('已复制')
  } catch (e) { ElMessage.error('复制失败，请手动选中复制') }
}
// 点击密码框自动选中，方便手动复制
const selectPwd = (e) => { e.target.select() }
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
  isEdit.value = false
  editingId.value = null
  templateIdx.value = null
  selectedAccountIds.value = []
  Object.assign(form.value, {
    account_id: null, region: '', shape: 'VM.Standard.A1.Flex',
    ocpus: 4, memory_gb: 24, image_ocid: '', subnet_ocid: '',
    availability_domain: '', display_name: '', compartment_ocid: '', root_password: '',
    target_count: 1, interval_seconds: 60, open_all_ports: true, boot_volume_gb: 50,
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

// 编辑任务：填入现有值（root 密码不回填，留空表示不修改）
const openEdit = async (row) => {
  isEdit.value = true
  editingId.value = row.id
  templateIdx.value = null
  Object.assign(form.value, {
    account_id: row.account_id, region: row.region || '', shape: row.shape || 'VM.Standard.A1.Flex',
    ocpus: row.ocpus ?? 4, memory_gb: row.memory_gb ?? 24, image_ocid: row.image_ocid || '', subnet_ocid: row.subnet_ocid || '',
    availability_domain: row.availability_domain || '', display_name: row.display_name || '', compartment_ocid: '', root_password: '',
    target_count: row.target_count ?? 1, interval_seconds: row.interval_seconds ?? 60, open_all_ports: row.open_all_ports ?? true, boot_volume_gb: row.boot_volume_gb ?? 50,
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
  form.value.boot_volume_gb = 50  // 模板默认硬盘 50GB，可再手动改
}

// 卡片点击：选中并填入模板（shape 变化会触发 shapeArch watcher 自动刷新操作系统列表）
const selectTemplate = (i) => {
  templateIdx.value = i
  applyTemplate()
}

const submitCreate = async () => {
  // 新建多选模式：至少选一个账号；编辑模式：form.account_id 必填
  const accountIds = isEdit.value ? [form.value.account_id].filter(Boolean) : selectedAccountIds.value
  if (!accountIds.length || !form.value.region || !form.value.shape ||
      !form.value.image_ocid || !form.value.availability_domain) {
    ElMessage.warning('请填写必填项')
    return
  }
  creating.value = true
  try {
    if (isEdit.value) {
      // 编辑模式：组装 payload，root 密码留空表示不修改，compartment_ocid 不提交
      const payload = { ...form.value }
      delete payload.compartment_ocid
      if (!payload.root_password) delete payload.root_password
      await updateSnipeTask(editingId.value, payload)
      ElMessage.success('任务已更新')
    } else if (accountIds.length > 1) {
      // 批量创建：同一配置应用到多个账号
      const payload = { ...form.value }
      delete payload.account_id
      delete payload.compartment_ocid
      const res = await batchCreateSnipeTasks({ account_ids: accountIds, task: payload })
      const okNames = res.created.map((c) => c.account_name).join('、')
      if (res.failed.length) {
        const failInfo = res.failed.map((f) => `#${f.account_id}：${f.reason}`).join('；')
        ElMessage.warning(`批量创建完成：成功 ${res.created.length} 个（${okNames}），失败 ${res.failed.length} 个：${failInfo}`)
      } else {
        ElMessage.success(`批量创建成功：${res.created.length} 个任务（${okNames}）`)
      }
    } else {
      await createSnipeTask(form.value)
      ElMessage.success('任务已创建（待启动）')
    }
    createVisible.value = false
    load()
  } catch (e) {
    ElMessage.error((isEdit.value ? '保存失败：' : '创建失败：') + (e.response?.data?.detail || e.message))
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
    await ElMessageBox.confirm(`删除实例任务 #${row.id}？日志将一并删除。`, '确认', { type: 'warning' })
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

// 从账号管理"创建实例"跳转过来：预选账号并自动打开新建对话框
const handleAccountQuery = async () => {
  const qid = Number(route.query.account_id)
  if (qid) {
    await openCreate()
    selectedAccountIds.value = [qid]
    form.value.account_id = qid
    onAccountChange()
    // 用完清除 query，避免刷新页面重复弹框
    router.replace({ path: '/sniper' })
  }
}
onMounted(async () => {
  await load()
  await handleAccountQuery()
})
// 路由复用时（如已在开机管理页点创建实例），onMounted 不触发，用 watch 补
watch(() => route.query.account_id, async (v) => {
  if (v) await handleAccountQuery()
})
onUnmounted(stopLogPoll)
</script>

<style scoped>
/* ========== 新建实例对话框美化 ========== */
/* 对话框头部 / 内容 / 底部 */
:deep(.el-dialog__header) {
  padding: 18px 24px 14px;
  margin-right: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
:deep(.el-dialog__title) {
  font-weight: 600;
}
:deep(.el-dialog__body) {
  padding: 20px 24px 8px;
}
:deep(.el-dialog__footer) {
  padding: 14px 24px 20px;
  border-top: 1px solid var(--el-border-color-lighter);
}
/* 表单项统一间距，输入框圆角 */
:deep(.el-form-item) {
  margin-bottom: 18px;
}
:deep(.el-input__wrapper) {
  border-radius: 6px;
}
/* 表单分组标题 */
.form-group-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin: 20px 0 14px;
  padding-left: 10px;
  border-left: 3px solid var(--el-color-primary);
  line-height: 1.4;
}
.el-form > .form-group-title:first-child {
  margin-top: 0;
}
/* 模板卡片：2×2 网格 */
.tpl-cards-4 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.tpl-card {
  position: relative;
  border: 1px solid #dcdfe6;
  border-radius: 10px;
  padding: 14px 16px;
  cursor: pointer;
  background: #fff;
  transition: border-color 0.2s, box-shadow 0.2s, background-color 0.2s, transform 0.2s;
}
.tpl-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.1);
  border-color: var(--el-color-primary-light-5);
}
.tpl-card.active {
  border-color: var(--el-color-primary);
  background-color: var(--el-color-primary-light-9);
  box-shadow: 0 0 0 1px var(--el-color-primary), 0 4px 14px rgba(64, 158, 255, 0.15);
}
/* 选中卡片右上角打勾 */
.tpl-card.active::after {
  content: "✓";
  position: absolute;
  top: 10px;
  right: 12px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tpl-card-name {
  display: flex;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 6px;
  padding-right: 24px;
}
.tpl-card.active .tpl-card-name {
  color: var(--el-color-primary);
}
.tpl-card-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
/* 开机间隔快捷按钮：选中态加粗更明显 */
.interval-btns .el-button--primary {
  font-weight: 600;
}
/* 高级选项折叠区 */
:deep(.el-collapse) {
  border-top: none;
}
:deep(.el-collapse-item__header) {
  font-size: 13px;
  color: #606266;
}
.op-btns {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  align-items: center;
}
.op-btns .el-button {
  margin-left: 0;
}
</style>
