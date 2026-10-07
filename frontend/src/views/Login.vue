<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>OCI 多账号管理面板</h2>

      <!-- 未初始化：直接显示初始化表单 -->
      <div v-if="initialized === false">
        <p class="hint">首次使用，请先创建一个管理员账号</p>
        <el-form @submit.prevent="onInit">
          <el-form-item label="用户名">
            <el-input v-model="initForm.username" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="initForm.password" type="password" show-password placeholder="至少 6 位" />
          </el-form-item>
          <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
          <el-button type="primary" :loading="loading" style="width: 100%" @click="onInit">创建管理员并登录</el-button>
        </el-form>
      </div>

      <!-- 已初始化：登录表单 -->
      <div v-else-if="initialized === true">
        <el-form @submit.prevent="onLogin">
          <el-form-item label="用户名">
            <el-input v-model="form.username" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <el-form-item v-if="needTotp" label="动态验证码">
            <el-input v-model="form.totp_code" maxlength="6" placeholder="验证器 App 上的 6 位数字" />
          </el-form-item>
          <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
          <el-button type="primary" :loading="loading" style="width: 100%" @click="onLogin">登录</el-button>
        </el-form>
      </div>

      <div v-else style="text-align: center; color: #909399">加载中…</div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authStatus, initAdmin, login } from '../api/client'

const router = useRouter()
const initialized = ref(null)
const needTotp = ref(false)
const loading = ref(false)
const error = ref('')
const form = ref({ username: '', password: '', totp_code: '' })
const initForm = ref({ username: 'admin', password: '' })

onMounted(async () => {
  try {
    const s = await authStatus()
    initialized.value = s.initialized
  } catch (e) {
    error.value = '无法连接后端：' + (e.response?.data?.detail || e.message)
    initialized.value = true
  }
  if (localStorage.getItem('oci_token')) {
    router.replace('/')
  }
})

async function onLogin() {
  error.value = ''
  loading.value = true
  try {
    const r = await login(form.value)
    localStorage.setItem('oci_token', r.access_token)
    localStorage.setItem('oci_user', r.username)
    router.replace('/')
  } catch (e) {
    const detail = e.response?.data?.detail || '登录失败'
    if (detail === 'totp_required') {
      needTotp.value = true
      error.value = '该账号启用了双因素，请输入动态验证码'
    } else {
      error.value = detail
    }
  } finally {
    loading.value = false
  }
}

async function onInit() {
  error.value = ''
  loading.value = true
  try {
    await initAdmin({ username: initForm.value.username, password: initForm.value.password })
    // 初始化后直接用该账号登录
    form.value.username = initForm.value.username
    form.value.password = initForm.value.password
    initialized.value = true
    await onLogin()
  } catch (e) {
    error.value = e.response?.data?.detail || '初始化失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; }
.login-card { width: 380px; }
.login-card h2 { text-align: center; margin-bottom: 20px; }
.hint { color: #909399; font-size: 13px; text-align: center; margin-bottom: 12px; }
/* 登录/初始化表单标签对齐：固定宽度 + 两端对齐，"用户名"/"密码"/"动态验证码"冒号对齐 */
.login-card .el-form-item__label { width: 70px; text-align: justify; text-align-last: justify; }
</style>
