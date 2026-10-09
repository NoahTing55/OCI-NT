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

    <!-- 安全组：放行所有端口 -->
    <el-card header="安全组（放行所有端口）" style="margin-bottom: 16px">
      <el-form :inline="true" :model="portsForm">
        <el-form-item label="账号">
          <el-select v-model="portsForm.account_id" placeholder="选择账号" style="width: 180px" @change="loadPortsInstances">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="实例">
          <el-select v-model="portsForm.instance_id" placeholder="先选账号" style="width: 280px">
            <el-option
              v-for="i in portsInstances"
              :key="i.instance_id"
              :value="i.instance_id"
              :label="`${i.display_name}（${i.public_ip || '无公网IP'}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="opening" :disabled="!portsForm.instance_id" @click="submitOpenPorts">
            放行所有端口
          </el-button>
        </el-form-item>
      </el-form>
      <div style="font-size: 12px; color: #909399"></div>
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
  openAllPorts,
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

// ---------- 放行所有端口 ----------
const portsForm = ref({ account_id: null, instance_id: '' })
const portsInstances = ref([])
const opening = ref(false)

const loadPortsInstances = async () => {
  portsForm.value.instance_id = ''
  portsInstances.value = []
  if (!portsForm.value.account_id) return
  try {
    const data = await listInstances({ account_id: portsForm.value.account_id })
    portsInstances.value = data.items
  } catch (e) {
    ElMessage.error('加载实例失败：' + (e.response?.data?.detail || e.message))
  }
}

const submitOpenPorts = async () => {
  opening.value = true
  try {
    const r = await openAllPorts({
      account_id: portsForm.value.account_id,
      instance_id: portsForm.value.instance_id,
    })
    if (r.ok) ElMessage.success('已放行所有端口')
    else ElMessage.warning(r.message || '操作失败')
  } catch (e) {
    ElMessage.error('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    opening.value = false
  }
}

onMounted(async () => {
  accounts.value = await listAccounts().catch(() => [])
})
</script>
