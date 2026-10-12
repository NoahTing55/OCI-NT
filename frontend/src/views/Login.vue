<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-logo">☁</div>
        <h2>OCI 多账号管理面板</h2>
        <p class="login-sub">Oracle Cloud 多账号统一管理</p>
      </div>

      <!-- 未初始化：直接显示初始化表单 -->
      <div v-if="initialized === false">
        <p class="hint">首次使用，请先创建一个管理员账号</p>
        <el-form @submit.prevent="onInit">
          <el-form-item>
            <el-input v-model="initForm.username" autocomplete="username" placeholder="用户名" prefix-icon="User" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="initForm.password" type="password" show-password placeholder="密码（至少 6 位）" prefix-icon="Lock" size="large" />
          </el-form-item>
          <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
          <el-button type="primary" :loading="loading" class="login-btn" size="large" @click="onInit">创建管理员并登录</el-button>
        </el-form>
      </div>

      <!-- 已初始化：登录表单 -->
      <div v-else-if="initialized === true">
        <el-form @submit.prevent="onLogin">
          <el-form-item>
            <el-input v-model="form.username" autocomplete="username" placeholder="用户名" prefix-icon="User" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="form.password" type="password" show-password autocomplete="current-password" placeholder="密码" prefix-icon="Lock" size="large" />
          </el-form-item>
          <el-form-item v-if="needTotp">
            <el-input v-model="form.totp_code" maxlength="6" placeholder="验证器 App 上的 6 位数字" prefix-icon="Key" size="large" />
          </el-form-item>
          <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
          <el-button type="primary" :loading="loading" class="login-btn" size="large" @click="onLogin">登 录</el-button>
        </el-form>
      </div>

      <div v-else class="login-loading">加载中…</div>
    </div>
    <div class="login-foot">OCI-NT · 自托管多账号管理</div>
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
.login-wrap {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #001529 0%, #002140 55%, #003a6b 100%);
  position: relative;
  overflow: hidden;
}
.login-wrap::before {
  content: '';
  position: absolute;
  width: 600px;
  height: 600px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(64, 158, 255, 0.15) 0%, transparent 70%);
  top: -200px;
  right: -150px;
}
.login-wrap::after {
  content: '';
  position: absolute;
  width: 500px;
  height: 500px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(64, 158, 255, 0.1) 0%, transparent 70%);
  bottom: -180px;
  left: -120px;
}
.login-card {
  width: 400px;
  max-width: 92vw;
  background: #fff;
  border-radius: 16px;
  padding: 40px 36px 32px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
  position: relative;
  z-index: 1;
}
.login-brand { text-align: center; margin-bottom: 28px; }
.login-logo {
  width: 56px;
  height: 56px;
  margin: 0 auto 12px;
  border-radius: 16px;
  background: linear-gradient(135deg, #409eff 0%, #1677ff 100%);
  color: #fff;
  font-size: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(22, 119, 255, 0.35);
}
.login-brand h2 { margin: 0 0 6px; font-size: 22px; font-weight: 600; color: #1f2329; }
.login-sub { margin: 0; font-size: 13px; color: #909399; }
.hint { color: #909399; font-size: 13px; text-align: center; margin-bottom: 16px; }
.login-btn {
  width: 100%;
  background: linear-gradient(135deg, #409eff 0%, #1677ff 100%);
  border: none;
  border-radius: 8px;
  font-size: 16px;
  letter-spacing: 4px;
  box-shadow: 0 4px 14px rgba(22, 119, 255, 0.35);
}
.login-btn:hover { box-shadow: 0 6px 20px rgba(22, 119, 255, 0.45); }
.login-loading { text-align: center; color: #909399; padding: 20px 0; }
.login-foot {
  margin-top: 24px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
  position: relative;
  z-index: 1;
}
.login-card :deep(.el-input__wrapper) { border-radius: 8px; }
</style>
