import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

// 账号
export const listAccounts = () => api.get('/accounts').then((r) => r.data)
export const createAccount = (data) => api.post('/accounts', data).then((r) => r.data)
export const updateAccount = (id, data) => api.put(`/accounts/${id}`, data).then((r) => r.data)
export const deleteAccount = (id) => api.delete(`/accounts/${id}`).then((r) => r.data)
export const bindProxy = (id, proxy_id) => api.post(`/accounts/${id}/bind-proxy`, { proxy_id }).then((r) => r.data)

// 代理
export const listProxies = () => api.get('/proxies').then((r) => r.data)
export const createProxy = (data) => api.post('/proxies', data).then((r) => r.data)
export const deleteProxy = (id) => api.delete(`/proxies/${id}`).then((r) => r.data)

// 账户摘要
export const getAccountSummary = () => api.get('/account-summary').then((r) => r.data)

// 存活检查
export const checkAccount = (id) => api.post(`/health/check/${id}`).then((r) => r.data)
export const checkAllAccounts = () => api.post('/health/check-all').then((r) => r.data)

// 实例
export const listInstances = (params) => api.get('/instances', { params }).then((r) => r.data)
export const editInstance = (accountId, instanceId, data) =>
  api.put(`/instances/${accountId}/${encodeURIComponent(instanceId)}`, data).then((r) => r.data)

// 批量任务
export const createBatch = (data) => api.post('/batch', data).then((r) => r.data)
export const listBatchTasks = () => api.get('/batch').then((r) => r.data)
export const getBatchTask = (id) => api.get(`/batch/${id}`).then((r) => r.data)

// 换 IP
export const changeIp = (data) => api.post('/network/change-ip', data).then((r) => r.data)
export const openAllPorts = (data) => api.post('/network/open-all-ports', data).then((r) => r.data)

// Cloudflare
export const listCfTokens = () => api.get('/cloudflare/tokens').then((r) => r.data)
export const createCfToken = (data) => api.post('/cloudflare/tokens', data).then((r) => r.data)
export const deleteCfToken = (id) => api.delete(`/cloudflare/tokens/${id}`).then((r) => r.data)
export const listBindings = () => api.get('/cloudflare/bindings').then((r) => r.data)
export const createBinding = (data) => api.post('/cloudflare/bindings', data).then((r) => r.data)
export const deleteBinding = (id) => api.delete(`/cloudflare/bindings/${id}`).then((r) => r.data)
export const syncBinding = (id) => api.post(`/cloudflare/bindings/${id}/sync`).then((r) => r.data)
export const cfCheck = () => api.get('/cloudflare/check').then((r) => r.data)
export const cfSyncAll = () => api.post('/cloudflare/sync-all').then((r) => r.data)

// 抢机任务
export const listSnipeTasks = () => api.get('/sniper').then((r) => r.data)
export const createSnipeTask = (data) => api.post('/sniper', data).then((r) => r.data)
export const updateSnipeTask = (id, data) => api.put(`/sniper/${id}`, data).then((r) => r.data)
export const startSnipeTask = (id) => api.post(`/sniper/${id}/start`).then((r) => r.data)
export const pauseSnipeTask = (id) => api.post(`/sniper/${id}/pause`).then((r) => r.data)
export const deleteSnipeTask = (id) => api.delete(`/sniper/${id}`).then((r) => r.data)
export const getSnipeLogs = (id, params) => api.get(`/sniper/${id}/logs`, { params }).then((r) => r.data)
export const getSnipeTemplates = () => api.get('/sniper/templates').then((r) => r.data)

// OCI 选项查询（抢机/批量创建表单级联下拉：可用域、平台镜像、子网、compartment）
export const getOciAvailabilityDomains = (params) =>
  api.get('/oci-options/availability-domains', { params }).then((r) => r.data)
export const getOciImages = (params) =>
  api.get('/oci-options/images', { params }).then((r) => r.data)
export const getOciSubnets = (params) =>
  api.get('/oci-options/subnets', { params }).then((r) => r.data)
export const getOciCompartments = (params) =>
  api.get('/oci-options/compartments', { params }).then((r) => r.data)

// 系统设置
export const getSettings = () => api.get('/settings').then((r) => r.data)
export const updateSettings = (data) => api.put('/settings', data).then((r) => r.data)
export const testTelegram = () => api.post('/settings/test-telegram').then((r) => r.data)

// 鉴权
export const authStatus = () => api.get('/auth/status').then((r) => r.data)
export const login = (data) => api.post('/auth/login', data).then((r) => r.data)
export const initAdmin = (data) => api.post('/auth/init', data).then((r) => r.data)
export const getMe = () => api.get('/auth/me').then((r) => r.data)
export const changePassword = (data) => api.post('/auth/change-password', data).then((r) => r.data)
export const totpSetup = () => api.post('/auth/totp/setup').then((r) => r.data)
export const totpEnable = (code) => api.post('/auth/totp/enable', { totp_code: code }).then((r) => r.data)
export const totpDisable = (password) => api.post('/auth/totp/disable', { password }).then((r) => r.data)

// 请求拦截器：自动带上 JWT
api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('oci_token')
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

// 401 → 清掉 token 回登录页（登录页本身除外，避免循环跳转）
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response && err.response.status === 401 && !window.location.pathname.startsWith('/login')) {
      localStorage.removeItem('oci_token')
      localStorage.removeItem('oci_user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)
