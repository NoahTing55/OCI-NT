<template>
  <el-dialog v-model="visible" :title="`SSH 连接信息 - ${instanceName}`" width="500px" @closed="onClose">
    <el-descriptions :column="1" border>
      <el-descriptions-item label="主机 IP">{{ hostIp }}</el-descriptions-item>
      <el-descriptions-item label="用户名">root</el-descriptions-item>
      <el-descriptions-item label="密码">
        <el-input v-model="password" type="password" show-password readonly style="width: 220px;" />
        <el-button size="small" @click="copyPassword" style="margin-left: 8px;">复制密码</el-button>
      </el-descriptions-item>
      <el-descriptions-item label="SSH 命令">
        <code style="font-size: 12px;">ssh root@{{ hostIp }}</code>
        <el-button size="small" @click="copyCmd" style="margin-left: 8px;">复制</el-button>
      </el-descriptions-item>
    </el-descriptions>
    <div style="margin-top: 12px; font-size: 12px; color: #909399;">
      用系统终端、Xshell、PuTTY 等工具连接，粘贴密码登录即可。
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getTerminalInfo } from '../api/client.js'

const visible = ref(false)
const instanceName = ref('')
const hostIp = ref('')
const password = ref('')

const open = async (accountId, instanceId, displayName) => {
  instanceName.value = displayName || instanceId.slice(0, 12)
  hostIp.value = '获取中...'
  password.value = ''
  visible.value = true
  try {
    const r = await getTerminalInfo(accountId, instanceId)
    hostIp.value = r.public_ip || '未知'
    password.value = r.password || ''
    if (!r.password) ElMessage.warning('未找到该实例的 root 密码')
  } catch (e) {
    hostIp.value = '获取失败'
    ElMessage.error('获取连接信息失败')
  }
}

const copyText = async (text, msg) => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(msg)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    ElMessage.success(msg)
  }
}
const copyPassword = () => copyText(password.value, '密码已复制')
const copyCmd = () => copyText(`ssh root@${hostIp.value}`, 'SSH 命令已复制')

const onClose = () => {
  password.value = ''
}

defineExpose({ open })
</script>
