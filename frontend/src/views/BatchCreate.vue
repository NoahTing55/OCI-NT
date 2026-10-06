<template>
  <div>
    <div style="margin-bottom: 12px">
      <el-button type="primary" @click="openWizard">新建批量创建任务</el-button>
      <el-button @click="load" :loading="loading">刷新</el-button>
      <span style="margin-left: 8px; font-size: 12px; color: #909399">
        先选配置、再选账号：实例名 = 命名前缀 + 序号（如 e5-01）
      </span>
    </div>

    <el-table :data="tasks" v-loading="loading" border>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="name" label="任务名" min-width="140" show-overflow-tooltip />
      <el-table-column prop="shape" label="Shape" width="190" />
      <el-table-column label="配置" width="110">
        <template #default="{ row }">{{ row.ocpus }}C / {{ row.memory_gb }}G</template>
      </el-table-column>
      <el-table-column prop="retry_mode" label="模式" width="90">
        <template #default="{ row }">{{ row.retry_mode === 'retry' ? '失败重试' : '单次尝试' }}</template>
      </el-table-column>
      <el-table-column label="进度" width="170">
        <template #default="{ row }">
          <span style="color: #67c23a">✓{{ row.success_count }}</span>
          <span style="color: #f56c6c; margin-left: 6px">✗{{ row.fail_count }}</span>
          <span style="color: #909399; margin-left: 6px">/{{ row.total }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openProgress(row)">进度</el-button>
          <el-button size="small" type="warning" v-if="row.status === 'running'" @click="cancelTask(row)">取消</el-button>
          <el-button size="small" type="danger" :disabled="row.status === 'running'" @click="delTask(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建向导 -->
    <el-dialog v-model="wizardVisible" title="新建批量创建任务" width="860px" :close-on-click-modal="false">
      <el-steps :active="step" finish-status="success" style="margin-bottom: 16px">
        <el-step title="选配置" />
        <el-step title="选账号" />
        <el-step title="进度" />
      </el-steps>

      <!-- 第一步：选配置 -->
      <div v-if="step === 0">
        <el-form :model="form" label-width="130px">
          <el-form-item label="任务名称"><el-input v-model="form.name" placeholder="空则自动生成" /></el-form-item>
          <el-form-item label="Shape" required>
            <el-select v-model="form.shape" placeholder="选择机型" style="width: 100%" filterable allow-create>
              <el-option v-for="p in shapePresets" :key="p.shape" :value="p.shape" :label="`${p.shape}（${p.label}）`" />
            </el-select>
          </el-form-item>
          <el-form-item label="OCPU / 内存">
            <el-input-number v-model="form.ocpus" :min="0.5" :step="1" style="width: 130px" />
            <span style="margin: 0 8px">/</span>
            <el-input-number v-model="form.memory_gb" :min="1" :step="1" style="width: 130px" />
            <span style="margin-left: 4px">GB</span>
            <span style="margin-left: 8px; font-size: 12px; color: #909399">不做机型特判，配错靠 OCI 400 拦截</span>
          </el-form-item>
          <el-form-item label="每账号台数" required>
            <el-input-number v-model="form.count_per_account" :min="1" :max="10" />
          </el-form-item>
          <el-form-item label="命名前缀" required>
            <el-input v-model="form.name_prefix" placeholder="如 e5，实例名为 e5-01、e5-02…" />
          </el-form-item>
          <el-form-item label="重试模式" required>
            <el-radio-group v-model="form.retry_mode">
              <el-radio value="direct">单次尝试（direct）</el-radio>
              <el-radio value="retry">失败重试（retry，按抢机引擎的错误分类与退避）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="默认镜像 OCID"><el-input v-model="form.image_ocid" placeholder="ocid1.image.oc1...." /><template #extra><span style="color:#909399;font-size:12px">可留空，在第 2 步按账号单独填写</span></template></el-form-item>
          <el-form-item label="默认子网 OCID"><el-input v-model="form.subnet_ocid" placeholder="ocid1.subnet.oc1...." /><template #extra><span style="color:#909399;font-size:12px">可留空，在第 2 步按账号单独填写</span></template></el-form-item>
          <el-form-item label="默认可用域"><el-input v-model="form.availability_domain" placeholder="如 Uocm:AP-SEOUL-1-AD-1" /><template #extra><span style="color:#909399;font-size:12px">可留空，在第 2 步按账号单独填写</span></template></el-form-item>
          <el-form-item label="配置模板">
            <el-button @click="saveAsTemplate">保存当前为模板</el-button>
            <el-button @click="tplVisible = true">从模板载入</el-button>
          </el-form-item>
        </el-form>
      </div>

      <!-- 第二步：选账号 -->
      <div v-if="step === 1">
        <div style="margin-bottom: 8px; font-size: 12px; color: #909399">
          勾选账号；行内留空则用第一步的默认配置（区域留空用账号默认区域）。
          <el-button size="small" style="margin-left: 8px" @click="applyToAll">把第一行覆盖应用到全部</el-button>
        </div>
        <el-table :data="accountRows" border @selection-change="selChange">
          <el-table-column type="selection" width="45" />
          <el-table-column prop="name" label="账号" width="130" />
          <el-table-column label="区域覆盖" width="150">
            <template #default="{ row }"><el-input v-model="row.region" size="small" :placeholder="row.defaultRegion" /></template>
          </el-table-column>
          <el-table-column label="镜像覆盖" width="200">
            <template #default="{ row }"><el-input v-model="row.image_ocid" size="small" placeholder="空=用默认" /></template>
          </el-table-column>
          <el-table-column label="子网覆盖" width="200">
            <template #default="{ row }"><el-input v-model="row.subnet_ocid" size="small" placeholder="空=用默认" /></template>
          </el-table-column>
          <el-table-column label="可用域覆盖">
            <template #default="{ row }"><el-input v-model="row.availability_domain" size="small" placeholder="空=用默认" /></template>
          </el-table-column>
        </el-table>
        <div style="margin-top: 8px; font-size: 13px">
          预计创建：<b>{{ selected.length }} 个账号 × 每账号 {{ form.count_per_account }} 台 = {{ selected.length * form.count_per_account }} 台</b>
        </div>
      </div>

      <!-- 第三步：进度 -->
      <div v-if="step === 2">
        <div style="margin-bottom: 10px">
          <el-tag type="success">成功 {{ detail.success_count }}</el-tag>
          <el-tag type="danger" style="margin-left: 8px">失败 {{ detail.fail_count }}</el-tag>
          <el-tag type="info" style="margin-left: 8px">取消 {{ detail.cancel_count }}</el-tag>
          <el-tag style="margin-left: 8px">共 {{ detail.total }} 台</el-tag>
          <el-tag :type="statusType(detail.status)" style="margin-left: 8px">{{ statusText(detail.status) }}</el-tag>
          <el-button v-if="detail.status === 'running'" type="warning" size="small" style="margin-left: 12px" @click="cancelTask(detail)">取消任务</el-button>
        </div>
        <el-table :data="detail.items || []" border max-height="380">
          <el-table-column prop="account_name" label="账号" width="120" />
          <el-table-column prop="display_name" label="实例名" width="130" />
          <el-table-column prop="region" label="区域" width="130" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="attempts" label="尝试" width="60" />
          <el-table-column prop="instance_ocid" label="实例 OCID" min-width="180" show-overflow-tooltip />
          <el-table-column prop="last_error" label="最后错误" min-width="180" show-overflow-tooltip />
        </el-table>
      </div>

      <template #footer>
        <el-button v-if="step === 0" @click="wizardVisible = false">取消</el-button>
        <el-button v-if="step === 1" @click="step = 0">上一步</el-button>
        <el-button v-if="step === 0" type="primary" @click="toStep1">下一步：选账号</el-button>
        <el-button v-if="step === 1" type="primary" :loading="creating" @click="submit">提交创建</el-button>
        <el-button v-if="step === 2" @click="closeWizard">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 模板载入 -->
    <el-dialog v-model="tplVisible" title="从模板载入" width="640px">
      <el-table :data="templates" border>
        <el-table-column prop="name" label="模板名" />
        <el-table-column prop="shape" label="Shape" width="180" />
        <el-table-column label="配置" width="110">
          <template #default="{ row }">{{ row.ocpus }}C / {{ row.memory_gb }}G</template>
        </el-table-column>
        <el-table-column prop="retry_mode" label="模式" width="90">
          <template #default="{ row }">{{ row.retry_mode === 'retry' ? '失败重试' : '单次尝试' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="applyTpl(row)">载入</el-button>
            <el-button size="small" type="danger" @click="delTpl(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 进度抽屉（从列表打开） -->
    <el-drawer v-model="progressVisible" :title="`任务 #${detail.id} 进度`" size="900px" @closed="stopPoll">
      <div style="margin-bottom: 10px">
        <el-tag type="success">成功 {{ detail.success_count }}</el-tag>
        <el-tag type="danger" style="margin-left: 8px">失败 {{ detail.fail_count }}</el-tag>
        <el-tag type="info" style="margin-left: 8px">取消 {{ detail.cancel_count }}</el-tag>
        <el-tag style="margin-left: 8px">共 {{ detail.total }} 台</el-tag>
        <el-tag :type="statusType(detail.status)" style="margin-left: 8px">{{ statusText(detail.status) }}</el-tag>
        <el-button v-if="detail.status === 'running'" type="warning" size="small" style="margin-left: 12px" @click="cancelTask(detail)">取消任务</el-button>
        <span style="margin-left: 8px; font-size: 12px; color: #909399">每 3 秒自动刷新</span>
      </div>
      <el-table :data="detail.items || []" border max-height="70vh">
        <el-table-column prop="account_name" label="账号" width="120" />
        <el-table-column prop="display_name" label="实例名" width="130" />
        <el-table-column prop="region" label="区域" width="130" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="attempts" label="尝试" width="60" />
        <el-table-column prop="instance_ocid" label="实例 OCID" min-width="180" show-overflow-tooltip />
        <el-table-column prop="last_error" label="最后错误" min-width="180" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAccounts,
  getShapePresets,
  createBatchCreateTask,
  listBatchCreateTasks,
  getBatchCreateTask,
  cancelBatchCreateTask,
  deleteBatchCreateTask,
  listBatchCreateTemplates,
  createBatchCreateTemplate,
  deleteBatchCreateTemplate,
} from '../api/client'

const STATUS_MAP = {
  pending: '待启动', running: '创建中', done: '已完成', cancelled: '已取消',
  success: '成功', failed: '失败',
}
const statusText = (s) => STATUS_MAP[s] || s
const statusType = (s) =>
  ({ running: 'primary', done: 'success', success: 'success', failed: 'danger', cancelled: 'info' }[s] || 'warning')

const tasks = ref([])
const loading = ref(false)
const load = async () => {
  loading.value = true
  try { tasks.value = await listBatchCreateTasks() } finally { loading.value = false }
}

// ---------- 向导 ----------
const wizardVisible = ref(false)
const step = ref(0)
const creating = ref(false)
const shapePresets = ref([])
const form = ref({
  name: '', shape: 'VM.Standard.E5.Flex', ocpus: 2, memory_gb: 16,
  count_per_account: 1, name_prefix: 'e5', retry_mode: 'direct',
  image_ocid: '', subnet_ocid: '', availability_domain: '',
})
const accountRows = ref([])
const selected = ref([])
const selChange = (rows) => { selected.value = rows }

const openWizard = async () => {
  step.value = 0
  wizardVisible.value = true
  const [presets, accounts] = await Promise.all([getShapePresets(), listAccounts()])
  shapePresets.value = presets
  accountRows.value = accounts.map((a) => ({
    account_id: a.id, name: a.name, defaultRegion: a.region,
    region: '', image_ocid: '', subnet_ocid: '', availability_domain: '',
  }))
  selected.value = []
}

const toStep1 = () => {
  if (!form.value.name_prefix.trim()) return ElMessage.error('命名前缀不能为空')
  // 第 1 步的三个 OCID 允许为空：可在第 2 步按账号单独填写，提交时再按账号校验
  step.value = 1
}

const applyToAll = () => {
  const first = accountRows.value[0]
  if (!first) return
  for (const r of accountRows.value) {
    r.region = first.region
    r.image_ocid = first.image_ocid
    r.subnet_ocid = first.subnet_ocid
    r.availability_domain = first.availability_domain
  }
  ElMessage.success('已把第一行的覆盖配置应用到全部账号')
}

const submit = async () => {
  if (!selected.value.length) return ElMessage.error('至少选择一个账号')
  // 按账号校验：每个已选账号的有效值（本行覆盖或第 1 步默认值）都不能为空
  const labels = { image_ocid: '镜像', subnet_ocid: '子网', availability_domain: '可用域' }
  for (const r of selected.value) {
    for (const f of ['image_ocid', 'subnet_ocid', 'availability_domain']) {
      if (!((r[f] || '').trim() || (form.value[f] || '').trim())) {
        return ElMessage.error(`账号「${r.name}」缺少${labels[f]}，请在第 1 步填默认值或在本行单独填写`)
      }
    }
  }
  creating.value = true
  try {
    const detail = await createBatchCreateTask({
      ...form.value,
      accounts: selected.value.map((r) => ({
        account_id: r.account_id, region: r.region,
        image_ocid: r.image_ocid, subnet_ocid: r.subnet_ocid,
        availability_domain: r.availability_domain,
      })),
    })
    ElMessage.success(`任务已创建并启动，共 ${detail.total} 台`)
    step.value = 2
    openProgress(detail, false)
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

const closeWizard = () => { wizardVisible.value = false; stopPoll() }

// ---------- 模板 ----------
const tplVisible = ref(false)
const templates = ref([])
const loadTemplates = async () => { templates.value = await listBatchCreateTemplates() }
const saveAsTemplate = async () => {
  try {
    const { value: name } = await ElMessageBox.prompt('模板名称', '保存为模板', { inputValue: form.value.name || form.value.shape })
    if (!name?.trim()) return
    await createBatchCreateTemplate({ ...form.value, name: name.trim() })
    ElMessage.success('模板已保存')
    loadTemplates()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}
const applyTpl = (t) => {
  Object.assign(form.value, {
    name: '', shape: t.shape, ocpus: t.ocpus, memory_gb: t.memory_gb,
    count_per_account: t.count_per_account, name_prefix: t.name_prefix,
    retry_mode: t.retry_mode, image_ocid: t.image_ocid || '',
    subnet_ocid: t.subnet_ocid || '', availability_domain: t.availability_domain || '',
  })
  tplVisible.value = false
  ElMessage.success(`已载入模板「${t.name}」`)
}
const delTpl = async (t) => {
  await ElMessageBox.confirm(`删除模板「${t.name}」？`, '确认', { type: 'warning' })
  await deleteBatchCreateTemplate(t.id)
  ElMessage.success('已删除')
  loadTemplates()
}

// ---------- 进度 ----------
const detail = ref({})
const progressVisible = ref(false)
let pollTimer = null
const stopPoll = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }
const refreshDetail = async () => {
  if (!detail.value.id) return
  try { detail.value = await getBatchCreateTask(detail.value.id) } catch (e) { /* 忽略轮询错误 */ }
}
const openProgress = (row, openDrawer = true) => {
  detail.value = row.id ? row : {}
  if (row.id) {
    refreshDetail()
    stopPoll()
    pollTimer = setInterval(refreshDetail, 3000)
  }
  if (openDrawer) progressVisible.value = true
}

const cancelTask = async (row) => {
  await ElMessageBox.confirm(`取消任务 #${row.id}？未完成的实例将不再创建。`, '确认', { type: 'warning' })
  await cancelBatchCreateTask(row.id)
  ElMessage.success('已取消')
  refreshDetail()
  load()
}
const delTask = async (row) => {
  await ElMessageBox.confirm(`删除任务 #${row.id}？`, '确认', { type: 'warning' })
  await deleteBatchCreateTask(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadTemplates() })
onUnmounted(stopPoll)
</script>
