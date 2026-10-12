<template>
  <!-- 登录页：无导航裸页 -->
  <router-view v-if="$route.path === '/login'" />

  <!-- 主布局 -->
  <el-container v-else style="height: 100vh">
    <!-- 顶部导航：深蓝渐变 -->
    <el-header class="app-header">
      <el-icon class="collapse-btn" @click="toggleCollapse" :title="collapsed ? '展开侧边栏' : '收起侧边栏'">
        <Fold v-if="!collapsed" /><Expand v-if="collapsed" />
      </el-icon>
      <el-icon class="logo-icon"><Cloudy /></el-icon>
      <span class="header-title">N&T的账号管理面板</span>
      <span style="flex: 1"></span>
      <span class="header-user">{{ username }}</span>
      <el-button size="small" @click="logout">退出</el-button>
    </el-header>
    <el-container>
      <!-- 侧边栏：可折叠 -->
      <el-aside :width="collapsed ? '64px' : '200px'" class="app-aside">
        <el-menu router :default-active="$route.path" :collapse="collapsed" background-color="transparent" text-color="rgba(255,255,255,0.65)" active-text-color="#fff">
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
<el-menu-item index="/system-logs">
            <el-icon><Setting /></el-icon><span>系统日志</span>
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
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Cloudy, DataBoard, User, Connection, Monitor, Share, Link, Aim, Lock, Setting, Fold, Expand } from '@element-plus/icons-vue'

const router = useRouter()
const username = computed(() => localStorage.getItem('oci_user') || '')

// 侧边栏折叠：localStorage 持久化
const collapsed = ref(localStorage.getItem('oci_sidebar_collapsed') === '1')
function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem('oci_sidebar_collapsed', collapsed.value ? '1' : '0')
}

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
.app-header .header-user {
  font-size: 13px;
  color: #bfcbd9;
  margin-right: 12px;
}

/* 侧边栏：渐变深蓝 + 悬停 + 选中高亮条 + 折叠动画 */
.app-aside {
  background: linear-gradient(180deg, #001529 0%, #002140 100%);
  transition: width 0.3s ease;
  overflow: hidden;
}
.app-header .collapse-btn {
  font-size: 20px;
  margin-right: 12px;
  cursor: pointer;
  color: #bfcbd9;
  transition: color 0.2s;
}
.app-header .collapse-btn:hover {
  color: #fff;
}

/* 表格：操作列 hover 才显示，减少视觉噪音 */
.table-hover-actions .el-table__row .hover-show {
  opacity: 0;
  transition: opacity 0.18s ease;
}
.table-hover-actions .el-table__row:hover .hover-show {
  opacity: 1;
}
.app-aside .el-menu {
  border-right: none;
  background: transparent;
  padding: 12px 0;
}
.app-aside .el-menu-item {
  border-left: 3px solid transparent;
  margin: 2px 0;
  height: 48px;
  line-height: 48px;
}
.app-aside .el-menu-item .el-icon {
  margin-right: 10px;
}
.app-aside .el-menu-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}
.app-aside .el-menu-item.is-active {
  color: #fff;
  background: linear-gradient(90deg, rgba(64, 158, 255, 0.28), transparent);
  border-left-color: #409eff;
}
</style>
