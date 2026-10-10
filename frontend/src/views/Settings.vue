<template>
  <div>
    <el-card v-for="g in groups" :key="g.key" style="margin-bottom: 16px">
      <template #header><b>{{ g.name }}</b></template>
      <el-form label-width="230px">
        <el-form-item v-for="item in g.items" :key="item.key" :label="item.label">
          <el-input
            v-if="item.secret"
            v-model="form[item.key]"
            type="password"
            show-password
            :placeholder="item.is_set ? '已设置（留空则不修改）' : '未设置'"
            style="width: 420px"
          />
          <el-input-number
            v-else-if="item.type === 'int'"
            v-model="form[item.key]"
            :min="item.min ?? 0"
            :step="1"
          />
          <el-time-picker
            v-else-if="item.type === 'time'"
            v-model="form[item.key]"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择时间"
            style="width: 160px"
          />
          <el-input v-else v-model="form[item.key]" style="width: 420px" />
          <div class="desc">{{ item.desc }}</div>
          <div v-if="item.secret && item.is_set" class="desc">状态：已设置</div>
        </el-form-item>
      </el-form>
      <el-button
        v-if="g.key === 'notify'"
        type="primary"
        plain
        :loading="testing"
        @click="doTest"
      >
        发送测试消息
      </el-button>
    </el-card>

    <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getSettings, updateSettings, testTelegram } from '../api/client'

const groups = ref([])
const form = ref({})
const saving = ref(false)
const testing = ref(false)

async function load() {
  const r = await getSettings()
  groups.value = r.groups || []
  const f = {}
  for (const g of groups.value) {
    for (const item of g.items) {
      // secret 项不回显明文，表单留空；非 secret 项回显当前值
      f[item.key] = item.secret ? '' : item.value
    }
  }
  form.value = f
}

async function save() {
  saving.value = true
  try {
    const payload = {}
    for (const g of groups.value) {
      for (const item of g.items) {
        const v = form.value[item.key]
        // secret 项只有用户填写了才提交（留空 = 不修改）
        if (item.secret) {
          if (v) payload[item.key] = v
        } else {
          payload[item.key] = v
        }
      }
    }
    const r = await updateSettings(payload)
    if (r.changed && r.changed.length) {
      ElMessage.success(`已保存，变更：${r.changed.join('、')}`)
    } else {
      ElMessage.success('已保存（无变更）')
    }
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doTest() {
  testing.value = true
  try {
    await testTelegram()
    ElMessage.success('测试消息已发送，请检查 Telegram')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.desc {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
