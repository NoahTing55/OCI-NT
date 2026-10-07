<template>
  <div class="dashboard">
    <!-- 顶部 4 张统计卡片 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card class="stat-card" shadow="hover" :body-style="{ padding: '18px' }">
          <div class="stat-body">
            <div class="stat-icon" :style="{ background: card.bg }">
              <el-icon><component :is="card.icon" /></el-icon>
            </div>
            <div>
              <div class="stat-num">{{ card.value }}</div>
              <div class="stat-label">{{ card.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 左：配额预警 -->
      <el-col :span="12">
        <el-card class="page-card" :body-style="{ padding: '20px' }" shadow="never">
          <div class="card-title">配额预警</div>
          <div v-if="quotaWarnings.length === 0" style="color: #909399; font-size: 13px; padding: 12px 0">
            暂无配额数据，点击「账号管理」页刷新摘要后查看
          </div>
          <div v-for="w in quotaWarnings" :key="w.account_id + w.key" style="margin-bottom: 14px">
            <div style="display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 6px">
              <span>{{ w.name }} · {{ w.label }}</span>
              <span :style="{ color: w.pct >= 80 ? '#f56c6c' : '#606266' }">{{ w.pct }}%</span>
            </div>
            <el-progress
              :percentage="w.pct"
              :stroke-width="10"
              :color="w.pct >= 90 ? '#f56c6c' : w.pct >= 80 ? '#e6a23c' : '#409eff'"
              :show-text="false"
            />
          </div>
        </el-card>
      </el-col>

      <!-- 右：最近动态 -->
      <el-col :span="12">
        <el-card class="page-card" :body-style="{ padding: '20px' }" shadow="never">
          <div class="card-title">最近动态</div>
          <div v-if="recentTasks.length === 0" style="color: #909399; font-size: 13px; padding: 12px 0">
            暂无抢机任务
          </div>
          <div
            v-for="t in recentTasks"
            :key="t.id"
            style="display: flex; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f2f5; font-size: 13px"
          >
            <el-tag :type="statusType(t.status)" size="small" style="margin-right: 10px; flex-shrink: 0">
              {{ statusText(t.status) }}
            </el-tag>
            <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
              {{ t.account_name || ('账号' + t.account_id) }} · {{ t.shape }} · {{ t.ocpus }}C/{{ t.memory_gb }}G
            </span>
            <span style="color: #909399; font-size: 12px; flex-shrink: 0">{{ fmtTime(t.created_at) }}</span>
          </div>
          <div style="margin-top: 12px; text-align: right">
            <el-link type="primary" :underline="false" @click="$router.push('/sniper')" style="font-size: 13px">
              查看全部抢机任务 →
            </el-link>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
// 总览页：统计卡片 + 配额预警 + 最近动态（只读聚合，不改业务逻辑）
import { ref, onMounted } from 'vue'
import { User, Server, Aim, CircleCheck } from '@element-plus/icons-vue'
import { listAccounts, listInstances, listSnipeTasks, getAccountSummary } from '../api/client'

const statCards = ref([
  { label: '账号总数', value: '-', icon: User, bg: 'linear-gradient(135deg,#409eff,#66b1ff)' },
  { label: '实例总数', value: '-', icon: Server, bg: 'linear-gradient(135deg,#67c23a,#85ce61)' },
  { label: '运行中抢机', value: '-', icon: Aim, bg: 'linear-gradient(135deg,#e6a23c,#eebe77)' },
  { label: '今日开机成功', value: '-', icon: CircleCheck, bg: 'linear-gradient(135deg,#f56c6c,#f78989)' },
])
const quotaWarnings = ref([])
const recentTasks = ref([])

// 状态文案与颜色（与 Sniper.vue 保持一致）
const STATUS_MAP = {
  pending: '待启动', running: '抢机中', paused: '已暂停',
  success: '已完成', stopped: '已停止', failed: '失败',
}
const statusText = (s) => STATUS_MAP[s] || s
const statusType = (s) =>
  ({ pending: 'info', running: 'warning', paused: '', success: 'success', stopped: 'info', failed: 'danger' }[s] || '')

const fmtTime = (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '')

onMounted(async () => {
  try {
    // 并发拉取聚合数据
    const [accounts, instRes, tasks, summary] = await Promise.all([
      listAccounts().catch(() => []),
      listInstances({}).catch(() => ({ items: [] })),
      listSnipeTasks().catch(() => []),
      getAccountSummary().catch(() => []),
    ])

    const items = instRes.items || instRes || []
    const running = (tasks || []).filter((t) => t.status === 'running').length
    const today = new Date().toISOString().slice(0, 10)
    const todaySuccess = (tasks || []).filter(
      (t) => t.status === 'success' && String(t.finished_at || '').slice(0, 10) === today,
    ).length

    statCards.value[0].value = (accounts || []).length
    statCards.value[1].value = Array.isArray(items) ? items.length : 0
    statCards.value[2].value = running
    statCards.value[3].value = todaySuccess

    // 配额预警：E2/A1/E5 使用率，取最高的列出
    const warnings = []
    for (const acc of summary || []) {
      const quotas = acc.quotas || {}
      for (const key of ['e2', 'a1', 'e5']) {
        const q = quotas[key]
        if (!q || q.available == null || q.available === 0) continue
        const pct = Math.round(((q.used || 0) / q.available) * 100)
        if (pct >= 50) {
          warnings.push({
            account_id: acc.account_id,
            name: acc.name,
            key,
            label: { e2: 'E2', a1: 'ARM A1', e5: 'E5' }[key],
            pct: Math.min(pct, 100),
          })
        }
      }
    }
    warnings.sort((a, b) => b.pct - a.pct)
    quotaWarnings.value = warnings.slice(0, 8)

    // 最近 5 条抢机任务
    const sorted = [...(tasks || [])].sort((a, b) =>
      String(b.created_at || '').localeCompare(String(a.created_at || '')),
    )
    recentTasks.value = sorted.slice(0, 5)
  } catch (e) {
    console.error('总览数据加载失败', e)
  }
})
</script>

<style scoped>
.dashboard {
  padding: 4px;
}
</style>
