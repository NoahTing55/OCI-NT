<template>
  <div>
    <el-button type="primary" @click="openCreate">新建账号</el-button>
    <el-button @click="openBatchImport">批量导入</el-button>
    <el-button @click="checkAll" :loading="checkingAll">全部存活检查</el-button>
    <el-button @click="loadSummary" :loading="summaryLoading">刷新摘要</el-button>
    <el-button type="warning" @click="openBatchSubscribe" :disabled="!selectedAccounts.length">批量订阅区域</el-button>

    <!-- 账户摘要卡片 -->
    <div v-if="summary.length" class="summary-cards">
      <el-card v-for="s in summary" :key="s.account_id" class="summary-card" shadow="hover" @click="goInstances(s.account_id)">
        <div class="summary-head">
          <span class="summary-name">{{ s.name }}</span>
          <el-tag size="small" type="info">{{ s.region }}</el-tag>
        </div>
        <div class="summary-stats">
          <div class="stat"><div class="stat-num">{{ s.running_count }}<span class="stat-sub">/{{ s.instance_count }}</span></div><div class="stat-label">运行中/总数</div></div>
          <div class="stat"><div class="stat-num">{{ s.ocpu_used }}</div><div class="stat-label">OCPU 已用</div></div>
          <div class="stat"><div class="stat-num">{{ s.memory_used_gb }}<span class="stat-unit">G</span></div><div class="stat-label">内存已用</div></div>
        </div>
        <div class="summary-quotas">
          <div v-for="q in quotaRows(s)" :key="q.key" class="quota-row">
            <span class="quota-label">{{ q.label }}</span>
            <el-progress :percentage="q.pct" :color="q.color" :show-text="false" class="quota-bar" />
            <span class="quota-text">{{ q.text }}</span>
          </div>
        </div>
      </el-card>
    </div>
    <el-empty v-else-if="summaryLoaded" description="暂无摘要数据" :image-size="60" style="margin: 12px 0" />

    <el-table ref="acctTableRef" :data="accounts" v-loading="loading" style="margin-top: 12px" border stripe size="small" class="acct-table" @selection-change="onSelectionChange">
      <!-- 多选列：批量订阅区域用 -->
      <el-table-column type="selection" width="45" align="center" />
      <!-- 🛡️ 代理绑定状态：点击快速配置代理 -->
      <el-table-column width="52" align="center">
        <template #header><span title="绑定代理" style="opacity: .55">🛡️</span></template>
        <template #default="{ row }">
          <span class="shield-btn" :class="{ bound: !!row.proxy_id }"
            :title="row.proxy ? `已绑定：${row.proxy.name}，点击配置` : '未绑定代理，点击配置'"
            @click="openBind(row)">🛡️</span>
        </template>
      </el-table-column>
      <!-- 别名：点击单元格直接改 -->
      <el-table-column label="自定义名称" min-width="130" class-name="alias-col">
        <template #default="{ row }">
          <el-input v-if="isEditing(row.id, 'name')" v-model="cellVal" size="small" ref="cellInputRef"
            @keyup.enter="saveCell(row, 'name')" @blur="saveCell(row, 'name')" />
          <span v-else class="cell-editable" @click="startEdit(row, 'name')" :title="'点击修改：' + row.name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="tenancy_name" label="租户名称" min-width="140" show-overflow-tooltip />
      <el-table-column label="主区域" width="180">
        <template #default="{ row }">{{ fmtRegion(row.region) }}</template>
      </el-table-column>
      <!-- 成本：点击单元格直接改 -->
      <el-table-column label="账号成本" width="100" align="center">
        <template #default="{ row }">
          <el-input-number v-if="isEditing(row.id, 'cost')" v-model="cellVal" size="small" :min="0" :precision="2"
            @keyup.enter="saveCell(row, 'cost')" @blur="saveCell(row, 'cost')" ref="cellInputRef" style="width: 96px" />
          <span v-else class="cell-editable" @click="startEdit(row, 'cost')" title="点击修改成本">{{ fmtCost(row.cost) }}</span>
        </template>
      </el-table-column>
      <!-- 存活天数徽标 -->
      <el-table-column label="存活天数" width="90" align="center">
        <template #default="{ row }"><el-tag size="small" class="days-chip">{{ aliveDays(row) }}</el-tag></template>
      </el-table-column>
      <!-- 抢机任务状态 -->
      <el-table-column label="开机任务" width="110" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.snipe_task_status === 'running'" type="success" size="small" class="task-badge"><span class="spin-dot"></span>运行中</el-tag>
          <el-tag v-else-if="row.snipe_task_status === 'paused'" type="warning" size="small">已暂停</el-tag>
          <el-tag v-else type="info" size="small">无</el-tag>
        </template>
      </el-table-column>
      <!-- 账号类型：免费/试用/升级/未知（对标 OCI-Start） -->
      <el-table-column label="账号类型" min-width="120" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.account_type === 'free' || row.account_type === 'trial'" type="success" size="small">个人免费账户</el-tag>
          <el-tag v-else-if="row.account_type === 'upgraded'" type="primary" size="small">个人升级账户</el-tag>
          <el-tag v-else-if="row.account_type === 'paid'" type="primary" size="small">付费</el-tag>
          <el-tag v-else type="info" size="small">未知</el-tag>
        </template>
      </el-table-column>
      <!-- 实例数：点击跳转筛选 -->
      <el-table-column label="实例数" width="80" align="center">
        <template #default="{ row }">
          <el-link type="primary" @click="goInstances(row.id)">{{ row.instance_count ?? 0 }}</el-link>
        </template>
      </el-table-column>
      <el-table-column label="账号状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="STATUS[row.status]?.[1] || ''" size="small">{{ STATUS[row.status]?.[0] || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="120" align="center">
        <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
      </el-table-column>
      <!-- 操作收进下拉菜单 -->
            <el-table-column label="实例操作" width="110" align="center">
        <template #default="{ row }">
          <el-button size="small" type="warning" @click="goCreateInstance(row.id)"><el-icon><Aim /></el-icon>创建实例</el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="70" align="center">
        <template #default="{ row }">
          <el-dropdown trigger="click" @command="(cmd) => handleOp(cmd, row)">
            <el-button size="small">···</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="create">创建实例</el-dropdown-item>
                <el-dropdown-item command="edit">编辑</el-dropdown-item>
                <el-dropdown-item command="bind">绑定代理</el-dropdown-item>
                <el-dropdown-item command="check">存活检查</el-dropdown-item>
                <el-dropdown-item command="regions">区域订阅</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <!-- 导入 API：Config + PEM 一次填完，单栏无分栏 -->
    <el-dialog v-model="createVisible" title="导入 API" width="620px">
      <!-- 步骤 1：OCI API 配置 -->
          <div class="import-step">
            <span class="step-num">1</span>
            <span class="step-title">OCI API 配置</span>
          </div>
          <div class="step-desc">粘贴完整 Config，私钥可上传或直接粘贴在配置后。</div>

          <div class="import-sub">导入 Config 文件</div>
          <div class="file-drop">
            <div class="file-row">
              <el-button size="small" @click="triggerConfigSelect">选择文件</el-button>
              <span class="file-name" :class="{ empty: !configFileName }">{{ configFileName || '未选择任何文件' }}</span>
              <!-- 原生 file 输入，避免 el-upload 的额外请求；读取后自动解析 -->
              <input ref="configFileInput" type="file" accept=".config,config" style="display:none" @change="onConfigFileChange" />
            </div>
            <div class="file-hint">可直接选择文件名为 config 的 OCI 配置；也可以同时选择 PEM 私钥。<br />未选择文件，也可以在下方直接粘贴 Config。</div>
          </div>

          <div class="import-sub">完整 OCI Config</div>
          <div class="drop-zone" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="(e) => onDropSingleFile(e, 'config')">
            <el-input v-model="importForm.config" type="textarea" :rows="6"
              placeholder="粘贴 ~/.oci/config 内容，或拖拽 config 文件到此，如：&#10;[DEFAULT]&#10;user=ocid1.user.oc1...&#10;fingerprint=aa:bb:cc...&#10;tenancy=ocid1.tenancy.oc1...&#10;region=ap-seoul-1" />
            <div class="drop-hint">可拖拽 config 文件到此处</div>
          </div>
          <div class="region-hint">
            区域识别&nbsp;&nbsp;<span v-if="detectedRegion">已识别区域：<b>{{ detectedRegion }}</b></span><span v-else>等待输入 OCI Config</span><br />
            粘贴配置后自动识别 region，并在导入时再次由后端校验。
          </div>

          <!-- 步骤 2：PEM 私钥文件 -->
          <div class="import-step">
            <span class="step-num">2</span>
            <span class="step-title">PEM 私钥文件</span>
          </div>
          <div class="file-drop">
            <div class="file-row">
              <el-button size="small" @click="triggerPemSelect">选择文件</el-button>
              <span class="file-name" :class="{ empty: !pemFileName }">{{ pemFileName || '未选择任何文件' }}</span>
              <input ref="pemFileInput" type="file" accept=".pem,.key" style="display:none" @change="onPemFileChange" />
            </div>
            <div class="file-hint">如果 Config 中只有 key_file 路径，请在这里选择对应的 PEM 文件。</div>
          </div>
          <div class="import-sub">私钥 PEM（可选）</div>
          <div class="drop-zone" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="(e) => onDropSingleFile(e, 'privateKey')">
            <el-input v-model="importForm.privateKey" type="textarea" :rows="4"
              placeholder="-----BEGIN PRIVATE KEY-----，或拖拽 PEM 文件到此" show-password />
            <div class="drop-hint">可拖拽 PEM 文件到此处</div>
          </div>

          <!-- 解析结果：由 Config 自动解析，可手动修改 -->
          <div class="import-sub" style="display:flex;align-items:center;justify-content:space-between">
            <span>解析结果</span>
            <el-button size="small" @click="parseConfig">重新解析</el-button>
          </div>
          <div style="font-size:12px;color:#909399;margin-bottom:8px">以下字段由 Config 自动解析，可手动修改；私钥已同步到上方私钥框，只在提交瞬间传输，服务端加密入库，永不回显</div>
          <el-form :model="form" label-width="110px">
            <el-form-item label="自定义名称"><el-input v-model="form.name" placeholder="如 Phoenix-01，留空按 {城市}-{id}-{日期} 自动生成" /></el-form-item>
            <el-form-item label="Tenancy OCID"><el-input v-model="form.tenancy_ocid" placeholder="ocid1.tenancy.oc1.." /></el-form-item>
            <el-form-item label="User OCID"><el-input v-model="form.user_ocid" placeholder="ocid1.user.oc1.." /></el-form-item>
            <el-form-item label="指纹"><el-input v-model="form.fingerprint" placeholder="aa:bb:cc:.." /></el-form-item>
            <el-form-item label="区域">
              <el-select v-model="form.region" style="width:100%">
                <el-option v-for="r in REGIONS" :key="r" :value="r" :label="r" />
              </el-select>
            </el-form-item>
            <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
          </el-form>
      <el-form :model="form" label-width="110px" style="margin-top: 4px">
        <el-form-item label="绑定代理">
          <el-select v-model="form.proxy_id" placeholder="选择代理" style="width: 100%">
            <el-option :value="null" label="直连（不使用代理）" />
            <el-option
              v-for="p in proxies"
              :key="p.id"
              :value="p.id"
              :disabled="!!p.bound_account_name"
              :label="p.bound_account_name ? `${p.name}（已绑定：${p.bound_account_name}）` : `${p.name}（${p.scheme}://${p.host}:${p.port}）`"
            >
              <span>{{ p.name }}（{{ p.scheme }}://{{ p.host }}:{{ p.port }}）</span>
              <span v-if="p.bound_account_name" style="float: right; color: #e6a23c; font-size: 12px">已绑定：{{ p.bound_account_name }}</span>
              <span v-else style="float: right; color: #67c23a; font-size: 12px">{{ p.status === 'ok' ? `延迟 ${p.latency_ms ?? '-'}ms` : '未使用' }}</span>
            </el-option>
          </el-select>
          <div style="font-size:12px;color:#909399">默认选中第一个未使用的代理；一代理只能绑一个账号</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate" :loading="submitting">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入账号：多份 config 一次导入 -->
    <el-dialog v-model="batchImportVisible" title="批量导入账号" width="720px">
      <div style="font-size:12px;color:#909399;margin-bottom:10px">
        每组填写一份账号：自定义名称（可空，按 {城市}-{id}-{日期} 自动生成，如 Phoenix-3-20261008）+ Config 内容 + 私钥内容。
        文本框支持粘贴，也可把 config / PEM 文件直接拖拽到对应文本框。点"添加一组"可继续添加。
      </div>
      <div v-for="(item, idx) in batchItems" :key="idx" class="batch-item">
        <div class="batch-item-head">
          <span class="batch-item-title">账号 {{ idx + 1 }}</span>
          <el-button size="small" type="danger" link @click="removeBatchItem(idx)" v-if="batchItems.length > 1">删除</el-button>
        </div>
        <el-form label-width="90px" size="small">
          <el-form-item label="自定义名称">
            <el-input v-model="item.name" placeholder="可空，自动生成如 Phoenix-3-20261008" />
          </el-form-item>
          <el-form-item label="Config 内容">
            <div class="drop-zone" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="(e) => onDropFile(e, item, 'config_text')">
              <el-input v-model="item.config_text" type="textarea" :rows="4"
                placeholder="粘贴 ~/.oci/config 内容，或拖拽 config 文件到此" />
            </div>
          </el-form-item>
          <el-form-item label="私钥内容">
            <div class="drop-zone" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="(e) => onDropFile(e, item, 'private_key')">
              <el-input v-model="item.private_key" type="textarea" :rows="3"
                placeholder="-----BEGIN PRIVATE KEY-----，或拖拽 PEM 文件到此" show-password />
            </div>
          </el-form-item>
        </el-form>
      </div>
      <el-button @click="addBatchItem" style="margin-top:4px">+ 添加一组</el-button>
      <!-- 导入结果 -->
      <div v-if="batchResult" class="batch-result">
        <div v-if="batchResult.created.length" class="batch-ok">
          成功导入 {{ batchResult.created.length }} 个账号
        </div>
        <div v-if="batchResult.failed.length" class="batch-fail">
          <div>失败 {{ batchResult.failed.length }} 个：</div>
          <div v-for="f in batchResult.failed" :key="f.name" class="batch-fail-item">
            {{ f.name }}：{{ f.error }}
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="batchImportVisible = false">关闭</el-button>
        <el-button type="primary" @click="submitBatchImport" :loading="batchSubmitting">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 编辑账号（别名/区域/备注，不涉及密钥） -->
    <el-dialog v-model="editVisible" title="编辑账号" width="480px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="别名"><el-input v-model="editForm.name" placeholder="如 香港-01" /></el-form-item>
        <el-form-item label="区域">
          <el-select v-model="editForm.region" style="width:100%">
            <el-option v-for="r in REGIONS" :key="r" :value="r" :label="r" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.remark" /></el-form-item>
        <el-form-item label="注册时间">
          <el-date-picker v-model="editForm.registered_at" type="date" placeholder="账号真实注册日期（空则用添加时间）"
            style="width: 100%" value-format="YYYY-MM-DD" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit" :loading="editSubmitting">保存</el-button>
      </template>
    </el-dialog>

    <!-- 绑定代理 -->
    <el-dialog v-model="bindVisible" title="绑定代理（一账号一代理）" width="420px">
      <el-select v-model="bindProxyId" placeholder="选择代理（清空=解绑）" clearable style="width:100%">
        <el-option v-for="p in proxies" :key="p.id" :value="p.id" :label="`${p.name}（${p.scheme}://${p.host}:${p.port}）`" />
      </el-select>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" @click="submitBind" :loading="binding">确定</el-button>
      </template>
    </el-dialog>

    <!-- 区域订阅（仅升级账户） -->
    <el-dialog v-model="regionsVisible" :title="`区域订阅 - ${regionsAccountName}`" width="520px">
      <div class="import-sub">已订阅区域</div>
      <div v-loading="regionsLoading" style="min-height: 40px; margin-bottom: 16px">
        <el-tag v-for="r in subscribedRegions" :key="r.region_name" style="margin: 0 8px 8px 0"
          :type="r.is_home_region ? 'success' : ''">
          {{ r.region_name }}{{ r.is_home_region ? '（主区域）' : '' }}
        </el-tag>
        <span v-if="!regionsLoading && !subscribedRegions.length" style="color: #909399">暂无数据</span>
      </div>
      <div class="import-sub">订阅新区域</div>
      <div style="display: flex; gap: 8px">
        <el-select v-model="newRegion" placeholder="选择要订阅的区域" filterable style="flex: 1">
          <el-option v-for="r in unsubscribedRegions" :key="r.region_name" :value="r.region_name" :label="r.region_name" />
        </el-select>
        <el-button type="primary" @click="doSubscribe" :loading="subscribing" :disabled="!newRegion">订阅</el-button>
      </div>
      <div style="font-size: 12px; color: #909399; margin-top: 8px">订阅后该区域可用于新建实例；仅升级账户可用。</div>
      <template #footer>
        <el-button @click="regionsVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 批量订阅区域（扩区）：勾选多个账号，一键订阅同一个新区域 -->
    <el-dialog v-model="batchSubVisible" title="批量订阅区域" width="560px">
      <div class="import-sub">将为以下 {{ selectedAccounts.length }} 个账号订阅新区域</div>
      <div style="margin-bottom: 16px">
        <el-tag v-for="a in selectedAccounts" :key="a.id" style="margin: 0 8px 8px 0">
          {{ a.name || `账号 #${a.id}` }}
        </el-tag>
      </div>
      <div class="import-sub">选择要订阅的区域</div>
      <el-select v-model="batchRegion" placeholder="选择要订阅的区域" filterable style="width: 100%">
        <el-option v-for="r in allRegions" :key="r.region_name" :value="r.region_name" :label="fmtRegion(r.region_name)" />
      </el-select>
      <!-- 批量结果明细 -->
      <div v-if="batchResults.length" style="margin-top: 16px">
        <div class="import-sub">订阅结果</div>
        <div v-for="r in batchResults" :key="r.account_id" style="display: flex; gap: 8px; align-items: center; padding: 4px 0">
          <span style="flex: 1">{{ r.account_name }}</span>
          <el-tag v-if="r.success" type="success" size="small">成功</el-tag>
          <el-tag v-else type="danger" size="small" :title="r.error">失败</el-tag>
          <span v-if="!r.success" style="font-size: 12px; color: #f56c6c; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" :title="r.error">{{ r.error }}</span>
        </div>
      </div>
      <template #footer>
        <el-button @click="batchSubVisible = false">关闭</el-button>
        <el-button type="primary" @click="doBatchSubscribe" :loading="batchSubscribing" :disabled="!batchRegion || !selectedAccounts.length">开始订阅</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'

// 区域中文名映射
const REGION_CN = {
  "us-phoenix-1": "凤凰城", "us-ashburn-1": "阿什本", "us-sanjose-1": "圣何塞", "us-chicago-1": "芝加哥",
  "ap-singapore-1": "新加坡", "ap-singapore-2": "新加坡西", "ap-tokyo-1": "东京", "ap-osaka-1": "大阪",
  "ap-seoul-1": "首尔", "ap-chuncheon-1": "春川", "ap-mumbai-1": "孟买", "ap-hyderabad-1": "海得拉巴",
  "ap-sydney-1": "悉尼", "ap-melbourne-1": "墨尔本", "ap-batam-1": "巴淡岛", "ap-kulai-2": "古来",
  "eu-frankfurt-1": "法兰克福", "eu-paris-1": "巴黎", "eu-marseille-1": "马赛", "eu-milan-1": "米兰",
  "eu-turin-1": "都灵", "eu-amsterdam-1": "阿姆斯特丹", "eu-madrid-1": "马德里", "eu-madrid-3": "马德里西",
  "eu-stockholm-1": "斯德哥尔摩", "eu-zurich-1": "苏黎世", "eu-jovanovac-1": "约瓦诺瓦茨",
  "uk-london-1": "伦敦", "uk-cardiff-1": "卡迪夫",
  "ca-toronto-1": "多伦多", "ca-montreal-1": "蒙特利尔",
  "sa-saopaulo-1": "圣保罗", "sa-vinhedo-1": "维涅杜", "sa-santiago-1": "圣地亚哥",
  "sa-valparaiso-1": "瓦尔帕莱索", "sa-bogota-1": "波哥大",
  "me-dubai-1": "迪拜", "me-abudhabi-1": "阿布扎比", "me-jeddah-1": "吉达", "me-riyadh-1": "利雅得",
  "il-jerusalem-1": "耶路撒冷",
  "mx-monterrey-1": "蒙特雷", "mx-queretaro-1": "克雷塔罗",
  "af-johannesburg-1": "约翰内斯堡", "af-casablanca-1": "卡萨布兰卡",
}
const fmtRegion = (r) => r ? `${r} ${REGION_CN[r] || ''}`.trim() : '-' 
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Aim } from '@element-plus/icons-vue'
import { listAccounts, createAccount, batchImportAccounts, updateAccount, deleteAccount, bindProxy, checkAccount, checkAllAccounts, listProxies, getAccountSummary, listRegionSubscriptions, subscribeRegion, batchSubscribeRegions, listOciRegions } from '../api/client.js'

const router = useRouter()
const REGIONS = ['ap-seoul-1', 'ap-tokyo-1', 'ap-singapore-1', 'ap-osaka-1', 'us-phoenix-1', 'us-ashburn-1', 'eu-frankfurt-1']
const STATUS = {
  healthy: ['正常', 'success'],
  key_invalid: ['密钥失效', 'danger'],
  forbidden: ['权限不足', 'warning'],
  not_found: ['用户不存在', 'warning'],
  network_error: ['网络异常', 'info'],
  unknown_error: ['未知异常', 'danger'],
  unchecked: ['未检查', ''],
}

const accounts = ref([])
const proxies = ref([])
const loading = ref(false)
const checkingAll = ref(false)
const createVisible = ref(false)
const submitting = ref(false)
const bindVisible = ref(false)
const binding = ref(false)
const bindProxyId = ref(null)
const bindAccount = ref(null)
const form = ref({ name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '', proxy_id: null })
// 默认选中第一个未使用的代理；没有可用时为 null（直连）
const defaultProxyId = () => {
  const free = proxies.value.find((p) => !p.bound_account_name)
  return free ? free.id : null
}

// ---------- 编辑账号（别名/区域/备注） ----------
const editVisible = ref(false)
const editSubmitting = ref(false)
const editId = ref(null)
const editForm = ref({ name: '', region: '', remark: '', registered_at: '' })

// ---------- API 导入（纯前端解析，零后端改动） ----------
// 单栏界面：Config + PEM + 解析结果一次填完，无 tab 分栏
const importForm = ref({ config: '', privateKey: '' })

// 文件选择器（原生 input，避免 el-upload 的额外请求）
const configFileInput = ref(null)
const pemFileInput = ref(null)
const configFileName = ref('')
const pemFileName = ref('')
const detectedRegion = ref('') // 解析出的区域，回显在"区域识别"提示区

const triggerConfigSelect = () => configFileInput.value?.click()
const triggerPemSelect = () => pemFileInput.value?.click()

// FileReader 读文本文件
const readTextFile = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(reader.result)
  reader.onerror = () => reject(reader.error)
  reader.readAsText(file)
})

// 选了 Config 文件：填入 textarea 并自动解析
const onConfigFileChange = async (e) => {
  const file = e.target.files?.[0]
  e.target.value = '' // 清空以便重复选择同一文件
  if (!file) return
  try {
    configFileName.value = file.name
    importForm.value.config = String(await readTextFile(file))
    parseConfig() // 自动解析并填入下方表单
  } catch (err) {
    ElMessage.error('读取文件失败：' + (err?.message || err))
  }
}

// 选了 PEM 私钥文件：填入私钥框
const onPemFileChange = async (e) => {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    pemFileName.value = file.name
    importForm.value.privateKey = String(await readTextFile(file)).trim()
    ElMessage.success('私钥已载入')
  } catch (err) {
    ElMessage.error('读取文件失败：' + (err?.message || err))
  }
}

// 简单 ini 解析：取 [DEFAULT]（无段头时按整段）中的 key=value
const parseConfig = () => {
  const text = importForm.value.config.trim()
  if (!text) return ElMessage.error('请先粘贴 ~/.oci/config 内容')
  const kv = {}
  let inDefault = !/^\s*\[/m.test(text) // 无段头则整段视为 DEFAULT
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#') || t.startsWith(';')) continue
    const sec = t.match(/^\[(.+)\]$/)
    if (sec) { inDefault = sec[1].trim().toUpperCase() === 'DEFAULT'; continue }
    if (!inDefault) continue
    const m = t.match(/^([^=]+?)\s*=\s*(.+?)\s*$/)
    if (m) kv[m[1].trim().toLowerCase()] = m[2].trim()
  }
  const missing = ['user', 'fingerprint', 'tenancy'].filter((k) => !kv[k])
  if (missing.length) return ElMessage.error('解析失败，缺少字段：' + missing.join('、'))
  form.value.user_ocid = kv.user
  form.value.fingerprint = kv.fingerprint
  form.value.tenancy_ocid = kv.tenancy
  if (kv.region) {
    // region 可能是 ap-seoul-1 或 oc1.ap-seoul-1 格式，取最后一段
    const r = kv.region.split('.').pop()
    form.value.region = REGIONS.includes(r) ? r : kv.region
    detectedRegion.value = form.value.region // 回显到"区域识别"提示区
  }
  if (importForm.value.privateKey.trim()) {
    form.value.private_key = importForm.value.privateKey.trim()
  }
  if (!form.value.name && kv.tenancy) {
    form.value.name = 'oci-' + kv.tenancy.replace(/[^a-zA-Z0-9]/g, '').slice(-6)
  }
  ElMessage.success('已解析并填入下方表单，请检查后保存')
}

// config 和 PEM 都有内容时自动解析并填入下方表单，无需手动点按钮（防抖 + 内容变化才触发）
let autoParseTimer = null
let lastAutoParseKey = ''
watch(
  () => [importForm.value.config, importForm.value.privateKey],
  ([cfg, pem]) => {
    clearTimeout(autoParseTimer)
    if (!cfg?.trim() || !pem?.trim()) return
    const key = cfg.trim() + '||' + pem.trim()
    if (key === lastAutoParseKey) return
    autoParseTimer = setTimeout(() => {
      lastAutoParseKey = key
      parseConfig()
    }, 800)
  },
  { deep: false }
)

const load = async () => {
  loading.value = true
  try {
    const [a, p] = await Promise.all([listAccounts(), listProxies()])
    accounts.value = a
    proxies.value = p
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ---------- 账户摘要 ----------
const summary = ref([])
const summaryLoading = ref(false)
const summaryLoaded = ref(false)
const QUOTA_META = [
  { key: 'e2', label: 'E2 配额' },
  { key: 'a1', label: 'ARM 配额' },
  { key: 'e5', label: 'E5 配额' },
]
// 配额行：计算进度条百分比和显示文本
const quotaRows = (s) => {
  return QUOTA_META.map((m) => {
    const q = (s.quotas || {})[m.key] || {}
    const avail = q.available, used = q.used
    if (avail == null || used == null) {
      return { key: m.key, label: m.label, pct: 0, color: '#dcdfe6', text: '未知' }
    }
    const total = avail + used
    const pct = total > 0 ? Math.round((used / total) * 100) : 0
    const color = pct >= 90 ? '#f56c6c' : pct >= 70 ? '#e6a23c' : '#67c23a'
    return { key: m.key, label: m.label, pct, color, text: `可用 ${avail}/${total}` }
  })
}
const loadSummary = async () => {
  summaryLoading.value = true
  try {
    summary.value = await getAccountSummary()
    summaryLoaded.value = true
  } catch (e) {
    ElMessage.error('摘要加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    summaryLoading.value = false
  }
}
// 卡片点击跳转到实例运维（按账号筛选）
const goInstances = (accountId) => {
  router.push({ path: '/instances', query: { account_id: accountId } })
}
// 跳转开机管理页并自动弹出新建实例框，账号预选为当前行
const goCreateInstance = (accountId) => {
  router.push({ path: '/sniper', query: { account_id: accountId } })
}
// ---------- 单元格点击编辑（别名/成本，OCI-Start 式：点击变输入框，回车/失焦保存） ----------
const editingCell = ref({ id: null, field: null })
const cellVal = ref('')
const cellInputRef = ref(null)
const isEditing = (id, field) => editingCell.value.id === id && editingCell.value.field === field
const startEdit = (row, field) => {
  editingCell.value = { id: row.id, field }
  cellVal.value = field === 'cost' ? Number(row.cost ?? 0) : (row[field] ?? '')
  nextTick(() => { try { cellInputRef.value?.focus() } catch (e) {} })
}
const cancelEdit = () => { editingCell.value = { id: null, field: null } }
const saveCell = async (row, field) => {
  if (!isEditing(row.id, field)) return  // 回车+失焦会触发两次，第二次直接返回
  const v = field === 'cost' ? Number(cellVal.value) : String(cellVal.value).trim()
  cancelEdit()
  if (field === 'name') {
    if (!v) return ElMessage.error('别名不能为空')
    if (v === row.name) return
  } else if (field === 'cost') {
    if (Number(row.cost ?? 0) === v) return
  }
  try {
    await updateAccount(row.id, { [field]: v })
    ElMessage.success('已保存')
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  }
}
// 成本格式化：保留 2 位小数
const fmtCost = (v) => Number(v ?? 0).toFixed(2)
// 操作下拉菜单分发
const handleOp = (cmd, row) => {
  if (cmd === 'create') goCreateInstance(row.id)
  else if (cmd === 'edit') openEdit(row)
  else if (cmd === 'bind') openBind(row)
  else if (cmd === 'check') checkOne(row)
  else if (cmd === 'regions') openRegions(row)
  else if (cmd === 'delete') remove(row)
}
// 存活天数：按自然日计算（避免时区/小时差导致少算一天）
const aliveDays = (row) => {
  const base = row.registered_at || row.created_at
  if (!base) return '-'
  const s = new Date(base)
  const n = new Date()
  const sd = new Date(s.getFullYear(), s.getMonth(), s.getDate())
  const nd = new Date(n.getFullYear(), n.getMonth(), n.getDate())
  const d = Math.round((nd - sd) / 86400000)
  return (d < 0 ? 0 : d) + ' 天'
}
// 创建时间只显示日期
const fmtDate = (v) => {
  if (!v) return '-'
  return String(v).slice(0, 10)
}

const openCreate = () => {
  form.value.proxy_id = defaultProxyId()
  importForm.value = { config: '', privateKey: '' }
  configFileName.value = ''
  pemFileName.value = ''
  detectedRegion.value = ''
  lastAutoParseKey = ''
  createVisible.value = true
}

const submitCreate = async () => {
  submitting.value = true
  try {
    await createAccount(form.value)
    ElMessage.success('账号已创建（私钥已加密存储）')
    createVisible.value = false
    form.value = { name: '', tenancy_ocid: '', user_ocid: '', fingerprint: '', private_key: '', region: 'ap-seoul-1', remark: '', proxy_id: null }
    load()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

// ---------- 批量导入账号 ----------
const batchImportVisible = ref(false)
const batchItems = ref([{ name: '', config_text: '', private_key: '' }])
const batchSubmitting = ref(false)
const batchResult = ref(null)

const openBatchImport = () => {
  batchItems.value = [{ name: '', config_text: '', private_key: '' }]
  batchResult.value = null
  batchImportVisible.value = true
}
const addBatchItem = () => {
  if (batchItems.value.length >= 100) return ElMessage.warning('一次最多导入 100 个')
  batchItems.value.push({ name: '', config_text: '', private_key: '' })
}
const removeBatchItem = (idx) => {
  batchItems.value.splice(idx, 1)
}
// 拖拽文件到文本框：读取文件内容填入对应字段
const onDropFile = (e, item, field) => {
  const file = e.dataTransfer?.files?.[0]
  if (!file) return
  e.currentTarget.classList.remove('drag-over')
  const reader = new FileReader()
  reader.onload = () => { item[field] = reader.result }
  reader.readAsText(file)
}
// 拖拽高亮
const onDragOver = (e) => {
  e.currentTarget.classList.add('drag-over')
}
const onDragLeave = (e) => {
  e.currentTarget.classList.remove('drag-over')
}
// 单账号导入：拖拽文件到文本框
const onDropSingleFile = (e, field) => {
  const file = e.dataTransfer?.files?.[0]
  if (!file) return
  const reader = new FileReader()
  e.currentTarget.classList.remove('drag-over')
  reader.onload = () => {
    importForm[field] = reader.result
    if (field === 'config') parseConfig()
  }
  reader.readAsText(file)
}
const submitBatchImport = async () => {
  const valid = batchItems.value.filter(i => i.config_text.trim() && i.private_key.trim())
  if (!valid.length) return ElMessage.error('请至少填写一组完整的 Config 和私钥')
  batchSubmitting.value = true
  batchResult.value = null
  try {
    const res = await batchImportAccounts(valid.map(i => ({
      name: i.name.trim(),
      config_text: i.config_text,
      private_key: i.private_key,
    })))
    batchResult.value = res
    if (res.created.length) {
      ElMessage.success(`成功导入 ${res.created.length} 个账号`)
      load()
    }
    if (res.failed.length && !res.created.length) {
      ElMessage.error('全部导入失败，见下方明细')
    }
  } catch (e) {
    ElMessage.error('导入失败：' + (e.response?.data?.detail || e.message))
  } finally {
    batchSubmitting.value = false
  }
}

const openEdit = (row) => {
  editId.value = row.id
  editForm.value = { name: row.name || '', region: row.region || '', remark: row.remark || '',
    registered_at: row.registered_at ? row.registered_at.slice(0, 10) : '' }
  editVisible.value = true
}

const submitEdit = async () => {
  if (!editForm.value.name.trim()) return ElMessage.error('别名不能为空')
  editSubmitting.value = true
  try {
    await updateAccount(editId.value, {
      name: editForm.value.name.trim(),
      region: editForm.value.region,
      remark: editForm.value.remark,
      // 空字符串表示清空（后端用 fields_set 区分"没传"和"清空"）
      ...(editForm.value.registered_at !== undefined ? { registered_at: editForm.value.registered_at || null } : {}),
    })
    ElMessage.success('已保存')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    editSubmitting.value = false
  }
}

const remove = async (row) => {  try {
    await ElMessageBox.confirm(`删除账号「${row.name}」？`, '确认', { type: 'warning' })
    await deleteAccount(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

const checkOne = async (row) => {
  row._checking = true
  try {
    const r = await checkAccount(row.id)
    ElMessage({ message: `${r.name}：${STATUS[r.status]?.[0] || r.status}（${r.message}）`, type: r.status === 'healthy' ? 'success' : 'warning' })
    load()
  } catch (e) {
    ElMessage.error('检查失败：' + (e.response?.data?.detail || e.message))
  } finally {
    row._checking = false
  }
}

const checkAll = async () => {
  checkingAll.value = true
  try {
    const results = await checkAllAccounts()
    const bad = results.filter((r) => r.status !== 'healthy').length
    ElMessage({ message: `检查完成：${results.length} 个账号，${bad} 个异常`, type: bad ? 'warning' : 'success' })
    load()
  } catch (e) {
    ElMessage.error('检查失败：' + (e.response?.data?.detail || e.message))
  } finally {
    checkingAll.value = false
  }
}

const openBind = (row) => {
  bindAccount.value = row
  bindProxyId.value = row.proxy_id
  bindVisible.value = true
}

const submitBind = async () => {
  binding.value = true
  try {
    await bindProxy(bindAccount.value.id, bindProxyId.value ?? null)
    ElMessage.success('绑定已更新')
    bindVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('绑定失败：' + (e.response?.data?.detail || e.message))
  } finally {
    binding.value = false
  }
}

// ---------- 区域订阅（仅升级账户） ----------
const regionsVisible = ref(false)
const regionsAccount = ref(null)
const regionsAccountName = ref('')
const subscribedRegions = ref([])
const allRegions = ref([])
const newRegion = ref('')
const regionsLoading = ref(false)
const subscribing = ref(false)
// 未订阅区域 = 全部区域去掉已订阅的
const unsubscribedRegions = computed(() => {
  const subbed = new Set(subscribedRegions.value.map((r) => r.region_name))
  return allRegions.value.filter((r) => !subbed.has(r.region_name))
})

const openRegions = async (row) => {
  regionsAccount.value = row
  regionsAccountName.value = row.name || `账号 #${row.id}`
  newRegion.value = ''
  regionsVisible.value = true
  await loadRegions()
}

const loadRegions = async () => {
  if (!regionsAccount.value) return
  regionsLoading.value = true
  try {
    // 订阅接口可能 404（无额外订阅或权限问题），失败时用主区域兜底，不报错
    const subs = await listRegionSubscriptions(regionsAccount.value.id).catch(() => null)
    const regions = await listOciRegions().catch(() => [])
    if (subs && subs.length) {
      subscribedRegions.value = subs
    } else {
      // 兜底：只显示主区域为已订阅
      const home = regionsAccount.value.region
      subscribedRegions.value = home ? [{ region_name: home, is_home_region: true, status: 'READY' }] : []
    }
    allRegions.value = regions || []
  } catch (e) {
    ElMessage.error('加载区域订阅失败：' + (e.response?.data?.detail || e.message))
  } finally {
    regionsLoading.value = false
  }
}

const doSubscribe = async () => {
  if (!newRegion.value || !regionsAccount.value) return
  subscribing.value = true
  try {
    await subscribeRegion(regionsAccount.value.id, newRegion.value)
    ElMessage.success(`已订阅区域 ${newRegion.value}`)
    newRegion.value = ''
    await loadRegions()
  } catch (e) {
    ElMessage.error('订阅失败：' + (e.response?.data?.detail || e.message))
  } finally {
    subscribing.value = false
  }
}

// ---------- 批量订阅区域（扩区） ----------
const selectedAccounts = ref([])  // 表格多选中的账号
const batchSubVisible = ref(false)
const batchRegion = ref('')
const batchResults = ref([])
const batchSubscribing = ref(false)

// 表格多选变化
const onSelectionChange = (rows) => {
  selectedAccounts.value = rows || []
}

// 打开批量订阅对话框：复用 /api/oci-regions 的区域列表
const openBatchSubscribe = async () => {
  if (!selectedAccounts.value.length) {
    ElMessage.warning('请先勾选要订阅区域的账号')
    return
  }
  batchRegion.value = ''
  batchResults.value = []
  batchSubVisible.value = true
  if (!allRegions.value.length) {
    allRegions.value = await listOciRegions().catch(() => []) || []
  }
}

// 执行批量订阅，展示每个账号的成功/失败明细
const doBatchSubscribe = async () => {
  if (!batchRegion.value || !selectedAccounts.value.length) return
  batchSubscribing.value = true
  batchResults.value = []
  try {
    const accountIds = selectedAccounts.value.map((a) => a.id)
    const data = await batchSubscribeRegions(accountIds, batchRegion.value)
    batchResults.value = data.results || []
    const okCount = batchResults.value.filter((r) => r.success).length
    const failCount = batchResults.value.length - okCount
    if (failCount === 0) {
      ElMessage.success(`批量订阅成功：${okCount} 个账号已订阅 ${batchRegion.value}`)
    } else {
      ElMessage.warning(`批量订阅完成：成功 ${okCount} 个，失败 ${failCount} 个`)
    }
  } catch (e) {
    ElMessage.error('批量订阅失败：' + (e.response?.data?.detail || e.message))
  } finally {
    batchSubscribing.value = false
  }
}

// 列宽拖拽：表头右边缘拉杆，拖动调整列宽
const acctTableRef = ref(null);
function initColumnResize() {
  nextTick(() => {
    const table = acctTableRef.value?.$el;
    if (!table) return;
    const headers = table.querySelectorAll('.el-table__header thead th');
    headers.forEach((th) => {
      if (th.querySelector('.col-resizer')) return;
      const resizer = document.createElement('div');
      resizer.className = 'col-resizer';
      th.style.position = 'relative';
      th.appendChild(resizer);
      resizer.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const startX = e.clientX;
        const startWidth = th.offsetWidth;
        const colIndex = Array.from(th.parentNode.children).indexOf(th);
        const onMove = (ev) => {
          const delta = ev.clientX - startX;
          const newWidth = Math.max(50, startWidth + delta);
          th.style.width = newWidth + 'px';
          table.querySelectorAll('.el-table__body tbody tr').forEach((tr) => {
            const td = tr.children[colIndex];
            if (td) td.style.width = newWidth + 'px';
          });
        };
        const onUp = () => {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
    });
  });
}

onMounted(() => { load(); initColumnResize(); });
watch(accounts, () => initColumnResize());
</script>

<style scoped>
.summary-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 12px;
}
.summary-card {
  width: 320px;
  cursor: pointer;
  border-radius: 10px;
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
}
.summary-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(64, 158, 255, 0.15);
}
.summary-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f2f5;
}
.summary-name {
  font-weight: 700;
  font-size: 16px;
  color: #303133;
}
.summary-stats {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: #303133;
  line-height: 1.2;
}
.stat-sub, .stat-unit {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.quota-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.quota-row .el-progress--line .el-progress-bar__outer {
  height: 10px !important;
  border-radius: 5px;
}
.quota-row .el-progress--line .el-progress-bar__inner {
  border-radius: 5px;
}
.quota-label {
  font-size: 12px;
  color: #606266;
  width: 52px;
  flex-shrink: 0;
}
.quota-bar {
  flex: 1;
}
.quota-text {
  font-size: 12px;
  color: #909399;
  width: 86px;
  text-align: right;
  flex-shrink: 0;
}

/* 文件拖拽区 */
.drop-zone {
  position: relative;
  border: 2px dashed transparent;
  border-radius: 6px;
  transition: border-color .2s, background .2s;
}
.drop-zone.drag-over {
  border-color: #409eff;
  background: rgba(64, 158, 255, .06);
}
.drop-hint {
  font-size: 11px;
  color: #a8abb2;
  text-align: center;
  padding: 4px 0 2px;
}
/* 导入对话框排版优化 */
.import-step {
  margin: 18px 0 10px;
}
.import-sub {
  font-weight: 600;
  margin: 14px 0 8px;
  font-size: 13px;
}
.file-drop {
  margin-bottom: 6px;
}
.region-hint {
  margin: 8px 0 4px;
  line-height: 1.6;
}
/* 列宽拖拽拉杆 */
.col-resizer {
  position: absolute;
  top: 0;
  right: -4px;
  width: 8px;
  height: 100%;
  cursor: col-resize;
  z-index: 10;
}
.col-resizer:hover {
  background: rgba(64, 158, 255, 0.35);
}
/* 表格紧凑精致 */
.acct-table {
  font-size: 13px;
}
/* 🛡️ 代理盾牌：未绑定灰色，已绑定蓝色 */
.shield-btn {
  cursor: pointer;
  font-size: 17px;
  filter: grayscale(1);
  opacity: .45;
  display: inline-block;
  transition: transform .15s;
}
.shield-btn.bound {
  filter: none;
  opacity: 1;
}
.shield-btn:hover {
  transform: scale(1.2);
}
/* 可点击编辑的单元格 */
.cell-editable {
  cursor: pointer;
  border-bottom: 1px dashed #c0c4cc;
}
.cell-editable:hover {
  color: #409eff;
  border-color: #409eff;
}
/* 存活天数徽标 */
.days-chip {
  font-weight: 600;
}
/* 抢机中：旋转圆点 */
.spin-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #67c23a;
  margin-right: 5px;
  vertical-align: 1px;
  animation: spinPulse 1.1s linear infinite;
}
@keyframes spinPulse {
  0% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.7); }
  100% { opacity: 1; transform: scale(1); }
}

/* 别名列紧凑间距 */
.alias-col .cell { padding-left: 8px; padding-right: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 批量导入 */
.batch-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 10px;
  background: #fafbfc;
}
.batch-item-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.batch-item-title {
  font-weight: 600;
  font-size: 13px;
}
.batch-result {
  margin-top: 12px;
  font-size: 13px;
}
.batch-ok {
  color: #67c23a;
  margin-bottom: 6px;
}
.batch-fail {
  color: #f56c6c;
}
.batch-fail-item {
  margin: 2px 0 2px 12px;
  font-size: 12px;
}
/* 新建账号-导入页：步骤头（序号圆圈 + 标题） */
.import-step { display: flex; align-items: center; margin: 20px 0 6px; }
.import-step:first-child { margin-top: 2px; }
.step-num {
  width: 26px; height: 26px; border-radius: 50%;
  background: #67c23a; color: #fff;
  font-size: 14px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
  margin-right: 10px; flex-shrink: 0;
}
.step-title { font-size: 15px; font-weight: 700; color: #303133; }
.step-desc { font-size: 12px; color: #909399; margin: 0 0 4px 36px; }
.import-sub { font-size: 13px; color: #303133; font-weight: 600; margin: 12px 0 8px; }

/* 文件选择虚线框（浅绿背景） */
.file-drop {
  border: 1.5px dashed #a9d18e;
  background: #f6fdf2;
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 6px;
}
.file-row { display: flex; align-items: center; gap: 10px; }
.file-name { font-size: 13px; color: #606266; word-break: break-all; }
.file-name.empty { color: #a8abb2; }
.file-hint { font-size: 12px; color: #909399; margin-top: 8px; line-height: 1.7; }

/* 区域识别提示条 */
.region-hint {
  background: #f4f4f5; border-radius: 6px;
  padding: 10px 12px; font-size: 12px; color: #909399;
  margin-top: 10px; line-height: 1.7;
}
.region-hint b { color: #67c23a; }
</style>
