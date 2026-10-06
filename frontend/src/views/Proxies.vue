<template>
  <div>
    <el-button type="primary" @click="createVisible = true">新建代理</el-button>
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

onMounted(load)
</script>
