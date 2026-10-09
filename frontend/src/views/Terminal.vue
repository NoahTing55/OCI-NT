<template>
  <el-dialog v-model="visible" :title="`终端 - ${instanceName}`" width="800px" :close-on-click-modal="false" @closed="onClose">
    <div ref="termRef" style="height: 480px; background: #1e1e1e; border-radius: 4px;"></div>
    <template #footer>
      <span style="font-size: 12px; color: #909399; margin-right: 12px">空闲 5 分钟自动断开</span>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, nextTick, onBeforeUnmount } from 'vue'
import { Terminal } from 'xterm'
import { FitAddon } from '@xterm/addon-fit'
import 'xterm/css/xterm.css'
import { ElMessage } from 'element-plus'

const visible = ref(false)
const termRef = ref(null)
const instanceName = ref('')
let term = null
let fitAddon = null
let ws = null

const open = (accountId, instanceId, displayName) => {
  instanceName.value = displayName || instanceId.slice(0, 12)
  visible.value = true
  nextTick(() => initTerminal(accountId, instanceId))
}

const initTerminal = (accountId, instanceId) => {
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)
  fitAddon.fit()
  term.focus()
  term.writeln('正在连接...\r')
  // 点击终端时聚焦
  termRef.value.addEventListener('click', () => term.focus())

  const token = localStorage.getItem('oci_token') || ''
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/api/terminal/ws/${accountId}/${instanceId}?token=${token}`)
  ws.onopen = () => term.writeln('连接已建立。\r')
  ws.onmessage = (e) => term.write(e.data)
  ws.onclose = () => term.writeln('\r\n连接已关闭。\r')
  ws.onerror = () => {
    term.writeln('\r\n连接出错。\r')
    ElMessage.error('终端连接失败')
  }
  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data)
  })
}

const onClose = () => {
  if (ws) { ws.close(); ws = null }
  if (term) { term.dispose(); term = null }
}

onBeforeUnmount(onClose)

defineExpose({ open })
</script>
