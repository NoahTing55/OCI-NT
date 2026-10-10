<template>
  <div class="p-4">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-bold">系统日志</h2>
      <div class="flex gap-2">
        <el-select v-model="current" placeholder="选择日志" style="width: 160px" @change="load">
          <el-option v-for="l in logs" :key="l.name" :label="l.name" :value="l.name" :disabled="!l.exists" />
        </el-select>
        <el-select v-model="lineCount" style="width: 110px" @change="load">
          <el-option :value="100" label="100 行" />
          <el-option :value="200" label="200 行" />
          <el-option :value="500" label="500 行" />
          <el-option :value="1000" label="1000 行" />
        </el-select>
        <el-button @click="load" :loading="loading">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="notice" :title="notice" type="info" :closable="false" class="mb-2" />
    <div class="log-box">
      <pre v-if="lines.length">{{ lines.join('\n') }}</pre>
      <div v-else class="text-gray-400 text-sm p-4">暂无日志</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const logs = ref([])
const current = ref('api')
const lines = ref([])
const lineCount = ref(200)
const loading = ref(false)
const notice = ref('')

async function fetchList() {
  const { data } = await axios.get('/api/system-logs')
  logs.value = data
  if (!logs.value.find(l => l.name === current.value)?.exists) {
    const first = logs.value.find(l => l.exists)
    if (first) current.value = first.name
  }
}

async function load() {
  loading.value = true
  notice.value = ''
  try {
    const { data } = await axios.get(`/api/system-logs/${current.value}`, { params: { lines: lineCount.value } })
    lines.value = data.lines || []
    if (data.message) notice.value = data.message
  } catch (e) {
    notice.value = e.response?.data?.detail || '读取失败'
    lines.value = []
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await fetchList()
  await load()
})
</script>

<style scoped>
.log-box {
  background: #1e1e1e;
  border-radius: 6px;
  max-height: calc(100vh - 180px);
  overflow: auto;
}
.log-box pre {
  color: #d4d4d4;
  font-size: 12px;
  line-height: 1.5;
  padding: 12px 16px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
