<template>
  <!-- 登录页：无导航裸页 -->
  <router-view v-if="$route.path === '/login'" />

  <!-- 主布局 -->
  <el-container v-else style="height: 100vh">
    <el-header style="background: #1f2d3d; color: #fff; display: flex; align-items: center">
      <span style="font-size: 18px; font-weight: bold">OCI 多账号管理面板</span>
      <span style="margin-left: 12px; font-size: 12px; color: #a0aec0">M4</span>
      <span style="flex: 1"></span>
      <span style="font-size: 13px; color: #bfcbd9; margin-right: 12px">{{ username }}</span>
      <el-button size="small" @click="logout">退出</el-button>
    </el-header>
    <el-container>
      <el-aside width="200px" style="background: #304156">
        <el-menu router :default-active="$route.path" background-color="#304156" text-color="#bfcbd9" active-text-color="#409eff">
          <el-menu-item index="/accounts">账号管理</el-menu-item>
          <el-menu-item index="/proxies">代理管理</el-menu-item>
          <el-menu-item index="/instances">实例运维</el-menu-item>
          <el-menu-item index="/network">网络 / 换 IP</el-menu-item>
          <el-menu-item index="/sniper">抢机任务</el-menu-item>
          <el-menu-item index="/batch-create">批量创建</el-menu-item>
          <el-menu-item index="/security">安全设置</el-menu-item>
        </el-menu>
      </el-aside>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const username = computed(() => localStorage.getItem('oci_user') || '')

function logout() {
  localStorage.removeItem('oci_token')
  localStorage.removeItem('oci_user')
  router.replace('/login')
}
</script>

<style>
body { margin: 0; }
</style>
