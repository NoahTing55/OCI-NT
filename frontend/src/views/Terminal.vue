<template>
  <el-dialog v-model="visible" :title="`终端 - ${instanceName}`" width="900px" :close-on-click-modal="false" @closed="onClose" @opened="onOpened">
    <div ref="termRef" style="height: 560px; background: #1e1e1e; border-radius: 4px;"></div>
    <template #footer>
      <span style="font-size: 12px; color: #909399; margin-right: 12px">空闲 5 分钟自动断开</span>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, nextTick } from 'vue'
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
let pendingAccountId = null
let pendingInstanceId = null

const open = (accountId, instanceId, displayName) => {
  instanceName.value = displayName || instanceId.slice(0, 12)
  pendingAccountId = accountId
  pendingInstanceId = instanceId
  visible.value = true
}

// dialog 打开动画完成后初始化（确保容器有尺寸）
const onOpened = () => {
  nextTick(() => initTerminal(pendingAccountId, pendingInstanceId))
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

  const token = localStorage.getItem('oci_token') || ''
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/api/terminal/ws/${accountId}/${instanceId}?token=${token}`)

  ws.onopen = () => term.writeln('WebSocket 已连接，等待 SSH...\r')

  ws.onmessage = (e) => {
    // 尝试解析 JSON 控制消息，否则当终端输出直接写
    try {
      const obj = JSON.parse(e.data)
      if (obj.type === 'connected') {
        term.writeln('SSH 连接成功。\r')
        return
      }
      if (obj.type === 'error') {
        term.writeln(`\r\n错误: ${obj.data}\r\n`)
        ElMessage.error(obj.data)
        return
      }
    } catch {
      // 不是 JSON，直接写终端
    }
    term.write(e.data)
  }

  ws.onclose = () => term.writeln('\r\n连接已关闭。\r')
  ws.onerror = () => {
    term.writeln('\r\n连接出错。\r')
    ElMessage.error('终端连接失败')
  }

  // 键盘输入 -> JSON 发送
  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }))
    }
  })

  // 窗口大小变化 -> 通知后端
  term.onResize(({ cols, rows }) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
}

const onClose = () => {
  if (ws) {
    try { ws.send(JSON.stringify({ type: 'disconnect' })) } catch {}
    ws.close()
    ws = null
  }
  if (term) { term.dispose(); term = null }
}

defineExpose({ open })
</script>
