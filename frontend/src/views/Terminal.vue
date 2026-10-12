<template>
  <el-dialog :style="{ maxWidth: '92vw' }" v-model="visible" title="SSH终端" width="95%" :close-on-click-modal="false" @closed="onClose" @opened="onOpened" class="term-dialog">
    <!-- 标签栏 -->
    <div class="term-tabs" v-if="tabs.length">
      <div
        v-for="t in tabs"
        :key="t.id"
        class="term-tab"
        :class="{ active: t.id === activeId }"
        @click="switchTab(t.id)"
      >
        <span class="term-tab-dot" :class="{ on: t.connected }"></span>
        <span class="term-tab-name">{{ t.name }}</span>
        <span class="term-tab-close" @click.stop="closeTab(t.id)">×</span>
      </div>
    </div>
    <!-- 工具栏 -->
    <div class="term-toolbar">
      <el-button size="small" type="success" @click="reconnect" :disabled="!activeTab || activeTab.connected">连接</el-button>
      <el-button size="small" type="danger" @click="disconnect" :disabled="!activeTab || !activeTab.connected">断开</el-button>
      <span class="term-status" :class="{ on: activeTab && activeTab.connected }">
        <i class="dot"></i>{{ activeTab && activeTab.connected ? '已连接' : '未连接' }}
      </span>
      <span style="flex: 1"></span>
      <el-button size="small" @click="changeFont(-1)">A-</el-button>
      <span style="font-size: 12px; color: #909399; margin: 0 6px;">{{ fontSize }}px</span>
      <el-button size="small" @click="changeFont(1)">A+</el-button>
      <el-button size="small" @click="clearScreen">清屏</el-button>
    </div>
    <!-- 终端区：每个标签独立容器 -->
    <div
      v-for="t in tabs"
      :key="'body-' + t.id"
      v-show="t.id === activeId"
      :ref="(el) => setTabEl(t.id, el)"
      class="term-body"
    ></div>
    <!-- 状态栏 -->
    <div class="term-statusbar">
      <span>{{ activeTab ? activeTab.statusText : '' }}</span>
      <span style="flex: 1"></span>
      <span>{{ activeTab ? `${activeTab.cols} x ${activeTab.rows}` : '' }}</span>
    </div>
    <template #footer>
      <span style="font-size: 12px; color: #8b949e; margin-right: 12px">空闲 5 分钟自动断开</span>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { Terminal } from 'xterm'
import { FitAddon } from '@xterm/addon-fit'
import 'xterm/css/xterm.css'
import { ElMessage } from 'element-plus'

const visible = ref(false)
const tabs = ref([])
const activeId = ref(null)
const fontSize = ref(16)
const tabEls = {}
let tabSeq = 0

const activeTab = computed(() => tabs.value.find((t) => t.id === activeId.value) || null)

const setTabEl = (id, el) => {
  if (el) tabEls[id] = el
  else delete tabEls[id]
}

// 对外接口：打开终端。已打开则新会话加入新标签，不覆盖
const open = (accountId, instanceId, displayName) => {
  const name = displayName || instanceId.slice(0, 12)
  const existing = tabs.value.find((t) => t.accountId === accountId && t.instanceId === instanceId)
  if (existing) {
    activeId.value = existing.id
    visible.value = true
    nextTick(() => refitActive())
    return
  }
  const tab = {
    id: ++tabSeq,
    name,
    accountId,
    instanceId,
    term: null,
    fit: null,
    ws: null,
    connected: false,
    statusText: '等待连接...',
    cols: 0,
    rows: 0,
  }
  tabs.value.push(tab)
  activeId.value = tab.id
  if (!visible.value) {
    visible.value = true // DOM 就绪后由 onOpened 统一初始化
  } else {
    nextTick(() => initTab(tab.id))
  }
}

const onOpened = () => {
  nextTick(() => {
    tabs.value.forEach((t) => initTab(t.id))
  })
}

const initTab = (tabId) => {
  const tab = tabs.value.find((t) => t.id === tabId)
  if (!tab || tab.term) return
  const el = tabEls[tabId]
  if (!el) return

  const term = new Terminal({
    cursorBlink: true,
    fontSize: fontSize.value,
    theme: { background: '#0d1117', foreground: '#c9d1d9', cursor: '#58a6ff' },
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  term.open(el)
  fit.fit()
  term.focus()
  tab.term = term
  tab.fit = fit
  tab.cols = term.cols
  tab.rows = term.rows
  tab.statusText = ''

  const token = localStorage.getItem('oci_token') || ''
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const dn = encodeURIComponent(tab.name || '')
  const ws = new WebSocket(`${proto}//${location.host}/api/terminal/ws/${tab.accountId}/${tab.instanceId}?display_name=${dn}`)
  tab.ws = ws

  ws.onopen = () => {
    // 首条消息发送 token 鉴权（不放 URL）
    ws.send(JSON.stringify({ type: 'auth', token }))
  }

  ws.onmessage = (e) => {
    try {
      const obj = JSON.parse(e.data)
      if (obj.type === 'connected') {
        tab.connected = true
        tab.statusText = '已连接'
        return
      }
      if (obj.type === 'error') {
        tab.term?.writeln(`\r\n\x1b[31m错误: ${obj.data}\x1b[0m\r\n`)
        tab.statusText = '连接失败'
        ElMessage.error(obj.data)
        return
      }
    } catch {}
    tab.term?.write(e.data)
  }
  ws.onclose = () => {
    tab.connected = false
    tab.statusText = '已断开'
    tab.term?.writeln('\r\n\x1b[33m连接已关闭。\x1b[0m\r\n')
  }
  ws.onerror = () => {
    tab.statusText = '连接出错'
    ElMessage.error('终端连接失败')
  }
  term.onData((data) => {
    if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
      tab.ws.send(JSON.stringify({ type: 'input', data }))
    }
  })
  term.onResize(({ cols: c, rows: r }) => {
    tab.cols = c
    tab.rows = r
    if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
      tab.ws.send(JSON.stringify({ type: 'resize', cols: c, rows: r }))
    }
  })
}

const refitActive = () => {
  const tab = activeTab.value
  if (tab && tab.fit) {
    tab.fit.fit()
    tab.cols = tab.term.cols
    tab.rows = tab.term.rows
    tab.term.focus()
  }
}

const switchTab = (id) => {
  if (activeId.value === id) return
  activeId.value = id
  nextTick(() => refitActive())
}

const disposeTab = (tab) => {
  if (tab.ws) {
    try { tab.ws.send(JSON.stringify({ type: 'disconnect' })) } catch {}
    try { tab.ws.close() } catch {}
    tab.ws = null
  }
  if (tab.term) {
    try { tab.term.dispose() } catch {}
    tab.term = null
  }
  tab.fit = null
  tab.connected = false
  // 注意：不删 tabEls，元素卸载时 ref 回调会自动清理；
  // reconnect 时容器还在，删了会导致 initTab 找不到元素
}

const closeTab = (id) => {
  const idx = tabs.value.findIndex((t) => t.id === id)
  if (idx < 0) return
  const tab = tabs.value[idx]
  disposeTab(tab)
  tabs.value.splice(idx, 1)
  if (!tabs.value.length) {
    activeId.value = null
    visible.value = false
  } else if (activeId.value === id) {
    activeId.value = tabs.value[Math.max(0, idx - 1)].id
    nextTick(() => refitActive())
  }
}

const reconnect = () => {
  const tab = activeTab.value
  if (!tab) return
  disposeTab(tab)
  // 保留标签位置，只重建连接
  nextTick(() => initTab(tab.id))
}
const disconnect = () => {
  const tab = activeTab.value
  if (tab && tab.ws) tab.ws.close()
}
const changeFont = (d) => {
  fontSize.value = Math.max(10, Math.min(24, fontSize.value + d))
  const tab = activeTab.value
  if (tab && tab.term) {
    tab.term.options.fontSize = fontSize.value
    tab.fit.fit()
  }
}
const clearScreen = () => {
  activeTab.value?.term?.clear()
}
const onClose = () => {
  // 对话框关闭：释放所有标签资源
  tabs.value.forEach((t) => disposeTab(t))
  tabs.value = []
  activeId.value = null
}

defineExpose({ open })
</script>

<style scoped>
.term-tabs {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  padding: 6px 8px 0;
  background: #161b22;
  border-radius: 6px 6px 0 0;
  overflow-x: auto;
}
.term-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px 8px 12px;
  background: #0d1117;
  color: #8b949e;
  border-radius: 8px 8px 0 0;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  border: 1px solid #30363d;
  border-bottom: none;
  max-width: 200px;
}
.term-tab.active {
  background: #1c2128;
  color: #c9d1d9;
}
.term-tab-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6e7681;
  flex-shrink: 0;
}
.term-tab-dot.on {
  background: #3fb950;
}
.term-tab-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
.term-tab-close {
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  border-radius: 4px;
  color: #6e7681;
}
.term-tab-close:hover {
  background: #30363d;
  color: #f85149;
}
.term-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: #161b22;
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
  padding: 8px;
  box-sizing: border-box;
}
.term-statusbar {
  display: flex;
  align-items: center;
  padding: 4px 10px;
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
  background: #161b22 !important;
  border: 1px solid #30363d !important;
  box-shadow: 0 0 0 1px #58a6ff33, 0 12px 48px rgba(0, 0, 0, 0.5) !important;
}
.term-dialog .el-dialog__body {
  padding: 0 8px !important;
}
.term-dialog .el-dialog__header {
  padding: 6px 10px !important;
  margin: 0 !important;
}
.term-dialog .el-dialog__header .el-dialog__title {
  color: #c9d1d9 !important;
  font-size: 14px !important;
}
.term-dialog .el-dialog__headerbtn .el-dialog__close {
  color: #8b949e !important;
}
.term-dialog .el-dialog__footer {
  padding: 4px 10px !important;
  background: #161b22 !important;
}
.term-dialog .el-dialog__footer .el-button {
  background: #21262d !important;
  border-color: #30363d !important;
  color: #c9d1d9 !important;
}
</style>
