<template>
  <div>
    <el-button type="primary" @click="createVisible = true">新建代理</el-button>
    <el-button @click="batchVisible = true">批量导入</el-button>
    <div style="font-size:12px;color:#909399;margin-top:8px">单API单代理：一个代理同一时间最多被一个账号绑定；socks5 会以 socks5h 方式使用，DNS 也走代理防泄漏</div>

    <el-table :data="proxies" v-loading="loading" style="margin-top: 12px" border>
      <el-table-column prop="name" label="名称" width="140" />
      <el-table-column prop="scheme" label="类型" width="90" />
      <el-table-column label="地址" width="220">
        <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
      </el-table-column>
      <el-table-column prop="username" label="用户名" width="120" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'ok' ? 'success' : row.status === 'fail' ? 'danger' : ''">
            {{ row.status === 'ok' ? '可用' : row.status === 'fail' ? '失效' : '未检查' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="latency_ms" label="延迟(ms)" width="100">
        <template #default="{ row }">{{ row.latency_ms ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" />
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建代理" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 HK-住宅-01" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.scheme" style="width:100%">
            <el-option value="http" label="http" />
            <el-option value="socks5" label="socks5（DNS 走代理）" />
          </el-select>
        </el-form-item>
        <el-form-item label="Host"><el-input v-model="form.host" placeholder="proxy.example.com" /></el-form-item>
        <el-form-item label="端口"><el-input-number v-model="form.port" :min="1" :max="65535" style="width:100%" /></el-form-item>
        <el-form-item label="用户名"><el-input v-model="form.username" placeholder="可空" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" show-password placeholder="可空，加密存储" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate" :loading="submitting">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入 -->
    <el-dialog v-model="batchVisible" title="批量导入代理" width="640px">
      <el-input v-model="batchText" type="textarea" :rows="8" placeholder="每行一个，支持：&#10;socks5://user:pass@host:port&#10;http://host:port&#10;host:port:user:pass&#10;host:port（默认 socks5）&#10;# 开头为注释，会跳过" />
      <div style="margin: 8px 0; font-size:12px;color:#909399">
        端口缺省：socks5→1080，http/https→8080；名称自动生成 proxy-1、proxy-2…
      </div>
      <el-button @click="parseBatch">解析预览</el-button>
      <el-table :data="batchList" style="margin-top:8px" border max-height="260">
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="scheme" label="类型" width="80" />
        <el-table-column label="地址" width="200">
          <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
        </el-table-column>
        <el-table-column prop="username" label="用户名" width="110" />
        <el-table-column prop="error" label="解析结果">
          <template #default="{ row }">
            <span v-if="row.error" style="color:#f56c6c">{{ row.error }}</span>
            <span v-else style="color:#67c23a">就绪</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button type="primary" @click="submitBatch" :loading="batchSubmitting"
          :disabled="!batchList.some((r) => !r.error)">确认导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProxies, createProxy, deleteProxy } from '../api/client.js'

const proxies = ref([])
const loading = ref(false)
const createVisible = ref(false)
const submitting = ref(false)
const form = ref({ name: '', scheme: 'http', host: '', port: 8080, username: '', password: '', remark: '' })

const load = async () => {
  loading.value = true
  try {
    proxies.value = await listProxies()
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const submitCreate = async () => {
  submitting.value = true
  try {
    await createProxy(form.value)
    ElMessage.success('代理已创建')
    createVisible.value = false
    form.value = { name: '', scheme: 'http', host: '', port: 8080, username: '', password: '', remark: '' }
    load()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

const remove = async (row) => {
  try {
    await ElMessageBox.confirm(`删除代理「${row.name}」？`, '确认', { type: 'warning' })
    await deleteProxy(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 批量导入 ----------
const batchVisible = ref(false)
const batchText = ref('')
const batchList = ref([])
const batchSubmitting = ref(false)
const DEFAULT_PORT = { socks5: 1080, http: 8080, https: 8080 }

// 解析单行，返回 {name,scheme,host,port,username,password} 或 {error}
const parseLine = (line, idx) => {
  const name = `proxy-${idx + 1}`
  let scheme = 'socks5', host = '', port = 0, username = '', password = ''
  const urlm = line.match(/^(socks5|http|https):\/\/(.*)$/i)
  if (urlm) {
    scheme = urlm[1].toLowerCase()
    let rest = urlm[2]
    const at = rest.lastIndexOf('@')
    if (at >= 0) {
      const auth = rest.slice(0, at).split(':')
      username = decodeURIComponent(auth[0] || '')
      password = decodeURIComponent(auth.slice(1).join(':'))
      rest = rest.slice(at + 1)
    }
    const hp = rest.split(':')
    host = hp[0]
    port = parseInt(hp[1] || '', 10) || 0
  } else {
    const parts = line.split(':')
    host = parts[0] || ''
    port = parseInt(parts[1] || '', 10) || 0
    username = parts[2] || ''
    password = parts.slice(3).join(':')
  }
  if (!host) return { error: '缺少 host' }
  if (!port) port = DEFAULT_PORT[scheme] || 1080
  if (port < 1 || port > 65535) return { error: '端口非法' }
  return { name, scheme, host, port, username, password, remark: '批量导入' }
}

const parseBatch = () => {
  const lines = batchText.value.split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
  if (!lines.length) return ElMessage.error('没有可解析的行')
  batchList.value = lines.map((l, i) => parseLine(l, i))
  const ok = batchList.value.filter((r) => !r.error).length
  ElMessage.info(`解析出 ${lines.length} 行，可导入 ${ok} 个`)
}

const submitBatch = async () => {
  const rows = batchList.value.filter((r) => !r.error)
  if (!rows.length) return
  batchSubmitting.value = true
  let ok = 0, fail = 0
  for (const r of rows) {
    try {
      await createProxy({ name: r.name, scheme: r.scheme, host: r.host, port: r.port,
        username: r.username, password: r.password, remark: r.remark })
      ok++
    } catch (e) { fail++ }
  }
  batchSubmitting.value = false
  batchVisible.value = false
  batchText.value = ''
  batchList.value = []
  load()
  ElMessage.success(`批量导入完成：成功 ${ok} 个，失败 ${fail} 个`)
}

onMounted(load)
</script>
