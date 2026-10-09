<template>
  <el-dialog v-model="visible" :title="`SSH终端 - ${instanceName}`" width="95%" :close-on-click-modal="false" @closed="onClose" @opened="onOpened" class="term-dialog">
    <!-- 工具栏 -->
    <div class="term-toolbar">
      <el-button size="small" type="success" @click="reconnect" :disabled="connected">连接</el-button>
      <el-button size="small" type="danger" @click="disconnect" :disabled="!connected">断开</el-button>
      <span class="term-status" :class="{ on: connected }">
        <i class="dot"></i>{{ connected ? '已连接' : '未连接' }}
      </span>
      <span style="flex: 1"></span>
      <el-button size="small" @click="changeFont(-1)">A-</el-button>
      <span style="font-size: 12px; color: #909399; margin: 0 6px;">{{ fontSize }}px</span>
      <el-button size="small" @click="changeFont(1)">A+</el-button>
      <el-button size="small" @click="clearScreen">清屏</el-button>
    </div>
    <!-- 终端区 -->
    <div ref="termRef" class="term-body"></div>
    <!-- 状态栏 -->
    <div class="term-statusbar">
      <span>{{ statusText }}</span>
      <span style="flex: 1"></span>
      <span>{{ cols }} x {{ rows }}</span>
    </div>
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
const connected = ref(false)
const statusText = ref('等待连接...')
const fontSize = ref(12)
const cols = ref(0)
const rows = ref(0)
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

const onOpened = () => {
  nextTick(() => initTerminal(pendingAccountId, pendingInstanceId))
}

const initTerminal = (accountId, instanceId) => {
  term = new Terminal({
    cursorBlink: true,
    fontSize: fontSize.value,
    theme: { background: '#0d1117', foreground: '#c9d1d9', cursor: '#58a6ff' },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)
  fitAddon.fit()
  term.focus()
  cols.value = term.cols
  rows.value = term.rows
  term.writeln('\x1b[32m正在连接...\x1b[0m')
  statusText.value = '正在连接...'

  const token = localStorage.getItem('oci_token') || ''
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/api/terminal/ws/${accountId}/${instanceId}?token=${token}`)

  ws.onmessage = (e) => {
    try {
      const obj = JSON.parse(e.data)
      if (obj.type === 'connected') {
        connected.value = true
        statusText.value = '已连接'
        return
      }
      if (obj.type === 'error') {
        term.writeln(`\r\n\x1b[31m错误: ${obj.data}\x1b[0m\r\n`)
        statusText.value = '连接失败'
        ElMessage.error(obj.data)
        return
      }
    } catch {}
    term.write(e.data)
  }
  ws.onclose = () => {
    connected.value = false
    statusText.value = '已断开'
    term.writeln('\r\n\x1b[33m连接已关闭。\x1b[0m\r\n')
  }
  ws.onerror = () => {
    statusText.value = '连接出错'
    ElMessage.error('终端连接失败')
  }
  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }))
    }
  })
  term.onResize(({ cols: c, rows: r }) => {
    cols.value = c
    rows.value = r
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols: c, rows: r }))
    }
  })
}

const reconnect = () => {
  onClose()
  nextTick(() => initTerminal(pendingAccountId, pendingInstanceId))
}
const disconnect = () => {
  if (ws) ws.close()
}
const changeFont = (d) => {
  fontSize.value = Math.max(10, Math.min(24, fontSize.value + d))
  if (term) {
    term.options.fontSize = fontSize.value
    fitAddon.fit()
  }
}
const clearScreen = () => {
  if (term) term.clear()
}
const onClose = () => {
  connected.value = false
  if (ws) {
    try { ws.send(JSON.stringify({ type: 'disconnect' })) } catch {}
    ws.close()
    ws = null
  }
  if (term) { term.dispose(); term = null }
}

defineExpose({ open })
</script>

<style scoped>
.term-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #161b22;
  border-radius: 6px 6px 0 0;
  margin-bottom: 0;
}
.term-status {
  font-size: 12px;
  color: #8b949e;
  display: flex;
  align-items: center;
  gap: 6px;
}
.term-status .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #8b949e;
  display: inline-block;
}
.term-status.on { color: #3fb950; }
.term-status.on .dot { background: #3fb950; }
.term-body {
  height: 60vh;
  min-height: 400px;
  background: #0d1117;
  padding: 12px;
  box-sizing: border-box;
}
.term-statusbar {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  background: #161b22;
  border-radius: 0 0 6px 6px;
  font-size: 12px;
  color: #8b949e;
}
</style>

<style>
.el-dialog.term-dialog {
  border-radius: 12px !important;
  margin-top: 5vh !important;
}
.term-dialog .el-dialog__body {
  padding: 4px 8px !important;
}
.term-dialog .el-dialog__header {
  padding: 8px 12px !important;
  margin: 0 !important;
}
.term-dialog .el-dialog__footer {
  padding: 6px 12px !important;
}
.term-dialog .el-dialog__title {
  font-size: 14px !important;
}
</style>
