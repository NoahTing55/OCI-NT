<template>
  <!-- 登录页：无导航裸页 -->
  <router-view v-if="$route.path === '/login'" />

  <!-- 主布局 -->
  <el-container v-else style="height: 100vh">
    <!-- 顶部导航：深蓝渐变 -->
    <el-header class="app-header">
      <el-icon class="logo-icon"><Cloudy /></el-icon>
      <span class="header-title">OCI 多账号管理面板</span>
      <span class="header-badge">M4</span>
      <span style="flex: 1"></span>
      <span class="header-user">{{ username }}</span>
      <el-button size="small" @click="logout">退出</el-button>
    </el-header>
    <el-container>
      <!-- 侧边栏 -->
      <el-aside width="200px" class="app-aside">
        <el-menu router :default-active="$route.path" background-color="#304156" text-color="#bfcbd9" active-text-color="#409eff">
          <el-menu-item index="/dashboard">
            <el-icon><DataBoard /></el-icon><span>总览</span>
          </el-menu-item>
          <el-menu-item index="/accounts">
            <el-icon><User /></el-icon><span>账号管理</span>
          </el-menu-item>
          <el-menu-item index="/proxies">
            <el-icon><Connection /></el-icon><span>代理管理</span>
          </el-menu-item>
          <el-menu-item index="/instances">
            <el-icon><Monitor /></el-icon><span>实例运维</span>
          </el-menu-item>
          <el-menu-item index="/network">
            <el-icon><Share /></el-icon><span>网络 / 换 IP</span>
          </el-menu-item>
          <el-menu-item index="/domains">
            <el-icon><Link /></el-icon><span>CF管理</span>
          </el-menu-item>
          <el-menu-item index="/sniper">
            <el-icon><Aim /></el-icon><span>开机管理</span>
          </el-menu-item>
          <el-menu-item index="/security">
            <el-icon><Lock /></el-icon><span>安全设置</span>
          </el-menu-item>
          <el-menu-item index="/settings">
            <el-icon><Setting /></el-icon><span>系统设置</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
// 主布局：侧边栏图标 + 渐变 header
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Cloudy, DataBoard, User, Connection, Monitor, Share, Link, Aim, Lock, Setting } from '@element-plus/icons-vue'

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

/* 顶部渐变 header */
.app-header {
  background: linear-gradient(135deg, #1f2d3d 0%, #2b3f55 100%);
  color: #fff;
  display: flex;
  align-items: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.app-header .logo-icon {
  font-size: 22px;
  margin-right: 10px;
  color: #409eff;
}
.app-header .header-title {
  font-size: 18px;
  font-weight: bold;
  letter-spacing: 1px;
}
.app-header .header-badge {
  margin-left: 12px;
  font-size: 12px;
  color: #a0aec0;
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
}
.app-header .header-user {
  font-size: 13px;
  color: #bfcbd9;
  margin-right: 12px;
}

/* 侧边栏 */
.app-aside {
  background: #304156;
}
.app-aside .el-menu {
  border-right: none;
}
.app-aside .el-menu-item .el-icon {
  margin-right: 8px;
}
</style>
