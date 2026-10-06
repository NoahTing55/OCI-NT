import { createRouter, createWebHistory } from 'vue-router'
import Accounts from '../views/Accounts.vue'
import Proxies from '../views/Proxies.vue'
import Instances from '../views/Instances.vue'
import Network from '../views/Network.vue'
import Sniper from '../views/Sniper.vue'
import BatchCreate from '../views/BatchCreate.vue'
import Login from '../views/Login.vue'
import Security from '../views/Security.vue'
import Settings from '../views/Settings.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login },
    { path: '/', redirect: '/accounts' },
    { path: '/accounts', component: Accounts },
    { path: '/proxies', component: Proxies },
    { path: '/instances', component: Instances },
    { path: '/network', component: Network },
    { path: '/sniper', component: Sniper },
    { path: '/batch-create', component: BatchCreate },
    { path: '/security', component: Security },
    { path: '/settings', component: Settings },
  ],
})

// 路由守卫：无 token 一律去登录页（登录页本身除外）
router.beforeEach((to) => {
  if (to.path === '/login') return true
  if (!localStorage.getItem('oci_token')) return '/login'
  return true
})

export default router
