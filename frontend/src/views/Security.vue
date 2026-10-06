<template>
  <div>
    <h2>安全设置</h2>

    <el-card style="margin-bottom: 16px">
      <template #header><span>双因素认证（TOTP）</span></template>
      <div v-if="me && !me.totp_enabled">
        <p style="color: #909399; font-size: 13px">绑定后登录时需要再输入验证器 App 上的 6 位动态码。</p>
        <div v-if="!qr">
          <el-button type="primary" @click="startSetup">开始绑定</el-button>
        </div>
        <div v-else>
          <p>1. 用验证器 App（Google Authenticator / Microsoft Authenticator 等）扫描二维码：</p>
          <img :src="qr" alt="TOTP 二维码" style="width: 200px; height: 200px; border: 1px solid #eee" />
          <p style="font-size: 12px; color: #909399">扫码失败可手动输入密钥：<el-tag>{{ secret }}</el-tag></p>
          <p>2. 输入 App 上的 6 位验证码完成启用：</p>
          <el-input v-model="code" maxlength="6" placeholder="6 位动态码" style="width: 200px; margin-right: 8px" />
          <el-button type="success" @click="enableTotp">启用</el-button>
        </div>
      </div>
      <div v-else-if="me && me.totp_enabled">
        <el-tag type="success">已启用</el-tag>
        <p style="color: #909399; font-size: 13px">解绑需要输入登录密码二次确认。</p>
        <el-input v-model="password" type="password" show-password placeholder="登录密码" style="width: 200px; margin-right: 8px" />
        <el-button type="danger" @click="disableTotp">解绑双因素</el-button>
      </div>
    </el-card>

    <el-card>
      <template #header><span>修改密码</span></template>
      <el-form label-width="100px" style="max-width: 400px">
        <el-form-item label="原密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-button type="primary" @click="doChangePassword">修改密码</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { changePassword, getMe, totpDisable, totpEnable, totpSetup } from '../api/client'

const me = ref(null)
const qr = ref('')
const secret = ref('')
const code = ref('')
const password = ref('')
const pwdForm = ref({ old_password: '', new_password: '' })

async function load() {
  me.value = await getMe()
}

async function startSetup() {
  const r = await totpSetup()
  qr.value = r.qr_data_uri
  secret.value = r.secret
}

async function enableTotp() {
  try {
    await totpEnable(code.value)
    ElMessage.success('双因素已启用')
    qr.value = ''
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启用失败')
  }
}

async function disableTotp() {
  try {
    await totpDisable(password.value)
    ElMessage.success('已解绑')
    password.value = ''
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '解绑失败')
  }
}

async function doChangePassword() {
  try {
    await changePassword(pwdForm.value)
    ElMessage.success('密码已修改')
    pwdForm.value = { old_password: '', new_password: '' }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '修改失败')
  }
}

onMounted(load)
</script>
