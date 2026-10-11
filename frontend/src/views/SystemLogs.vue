<template>
  <div class="syslog-page">
    <!-- 顶栏 -->
    <div class="syslog-topbar">
      <span class="syslog-title">>_ 系统日志</span>
      <div class="syslog-tabs">
        <span
          class="syslog-tab"
          :class="{ active: current === 'merged' }"
          @click="switchLog('merged')"
        >全部</span>
        <span
          v-for="l in logs"
          :key="l.name"
          class="syslog-tab"
          :class="{ active: current === l.name }"
          @click="switchLog(l.name)"
        >{{ l.name }}</span>
      </div>
      <span style="flex: 1"></span>
      <el-select v-model="levelFilter" style="width: 110px; margin-right: 8px" size="small" placeholder="级别">
        <el-option value="" label="全部" />
        <el-option value="DEBUG" label="Debug" />
        <el-option value="INFO" label="Info" />
        <el-option value="WARNING" label="Warning" />
        <el-option value="ERROR" label="Error" />
      </el-select>
      <el-button size="small" type="primary" @click="clearLogs" title="仅清除显示，下次刷新会重新加载">清屏</el-button>
      <el-button size="small" type="primary" @click="$router.back()">返回</el-button>
    </div>
    <!-- 输出区标题 -->
    <div class="syslog-outhead">
      <span class="syslog-outtitle">>_ 系统日志输出</span>
      <span style="flex: 1"></span>
      <span class="live-dot"></span>
      <span class="live-text">自动刷新中（10s）</span>
    </div>
    <!-- 日志区 -->
    <div ref="logBox" class="syslog-body">
      <div v-if="lines.length" class="syslog-lines">
        <div v-for="(line, i) in formattedLines" :key="i" class="syslog-line">{{ line }}</div>
      </div>
      <div v-else class="syslog-empty">暂无日志</div>
    </div>
    <!-- 底栏 -->
    <div class="syslog-footer">
      <span class="footer-time">◷ {{ nowTime }}</span>
      <span class="footer-count">☰ 共 {{ formattedLines.length }} 条</span>
      <span style="flex: 1"></span>
      <el-checkbox v-model="autoScroll" class="footer-check">自动滚动</el-checkbox>
      <span class="footer-live">实时更新中…</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { listSystemLogs, getSystemLog, getMergedSystemLog } from '../api/client.js'

const logs = ref([])
const current = ref('merged')
const lines = ref([])
const lineCount = 500
const loading = ref(false)
const autoScroll = ref(true)
const levelFilter = ref('')
const logBox = ref(null)
const nowTime = ref('')
let clockTimer = null
let refreshTimer = null

function fmtTime(line) {
  return line.replace(
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?/,
    '$1 $2'
  )
}

function parseLevel(line) {
  // 2026-10-11 04:31:22 WARNING:... -> WARNING
  let m = line.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR)/i)
  if (m) return m[1].toUpperCase()
  // 2026-10-10T16:10:31.717774[warning]... -> WARNING
  m = line.match(/\[(debug|info|warning|error)\]/i)
  if (m) return m[1].toUpperCase()
  // INFO:... -> INFO
  m = line.match(/^(DEBUG|INFO|WARNING|ERROR)[:]/i)
  if (m) return m[1].toUpperCase()
  return 'INFO'
}

const formattedLines = computed(() => {
  let result = lines.value.map(fmtTime)
  if (levelFilter.value) {
    result = result.filter(line => parseLevel(line) === levelFilter.value)
  }
  return result
})

function scrollBottom() {
  if (autoScroll.value && logBox.value) {
    nextTick(() => { logBox.value.scrollTop = logBox.value.scrollHeight })
  }
}

function clearLogs() {
  lines.value = []
}

function tick() {
  const d = new Date()
  nowTime.value = d.toTimeString().slice(0, 8)
}

async function fetchList() {
  const data = await listSystemLogs()
  logs.value = data
  if (!logs.value.find(l => l.name === current.value)?.exists) {
    const first = logs.value.find(l => l.exists)
    if (first) current.value = first.name
  }
}

function switchLog(name) {
  if (current.value === name) return
  current.value = name
  load()
}

async function load() {
  loading.value = true
  try {
    const data = current.value === 'merged'
      ? await getMergedSystemLog(lineCount.value)
      : await getSystemLog(current.value, lineCount.value)
    lines.value = data.lines || []
    scrollBottom()
  } catch (e) {
    lines.value = ['读取失败：' + (e.response?.data?.detail || e.message)]
  } finally {
    loading.value = false
  }
}

watch(lines, scrollBottom)

onMounted(async () => {
  tick()
  clockTimer = setInterval(tick, 1000)
  await fetchList()
  await load()
  // 每 10 秒自动刷新
  refreshTimer = setInterval(load, 10000)
})

onUnmounted(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.syslog-page {
  background: #0d1117;
  border: 1px solid #00ff41;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  min-height: 500px;
  overflow: hidden;
}
.syslog-topbar {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #161b22;
  border-bottom: 1px solid #00ff4133;
}
.syslog-tabs {
  display: flex;
  gap: 4px;
  margin-left: 16px;
}
.syslog-tab {
  padding: 4px 16px;
  font-size: 13px;
  color: #8b949e;
  cursor: pointer;
  border: 1px solid #30363d;
  border-radius: 4px;
  font-family: "Courier New", monospace;
}
.syslog-tab.active {
  color: #00ff41;
  border-color: #00ff41;
  background: #00ff4111;
}
.syslog-title {
  color: #00ff41;
  font-size: 16px;
  font-weight: bold;
  font-family: "Courier New", monospace;
}
.syslog-outhead {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  background: #0d1117;
  border-bottom: 1px solid #00ff4133;
}
.syslog-outtitle {
  color: #00ff41;
  font-size: 14px;
  font-family: "Courier New", monospace;
}
.live-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #00ff41;
  display: inline-block;
  margin-right: 6px;
  animation: blink 1.5s infinite;
}
@keyframes blink { 50% { opacity: 0.3; } }
.live-text {
  color: #00ff41;
  font-size: 12px;
}
.syslog-body {
  flex: 1;
  overflow-y: auto;
  background: #000;
  padding: 8px 12px;
}
.syslog-lines {
  font-family: "Courier New", monospace;
  font-size: 14px;
  line-height: 1.6;
  color: #00ff41;
}
.syslog-line {
  white-space: pre-wrap;
  word-break: break-all;
}
.syslog-empty {
  color: #666;
  font-size: 14px;
  padding: 20px;
  text-align: center;
}
.syslog-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  background: #0d1117;
  border-top: 1px solid #00ff4133;
  font-size: 12px;
  color: #00ff41;
  font-family: "Courier New", monospace;
}
.footer-check :deep(.el-checkbox__label) {
  color: #00ff41;
  font-size: 12px;
}
</style>
