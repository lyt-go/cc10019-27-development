<template>
  <section class="page" data-module="attr_dict">
    <header class="page-head">
      <div>
        <h2>属性字典管理</h2>
        <p class="page-desc">维护用户属性字段的字典定义、枚举管理与跨源归因配置，为属性字典提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记属性定义</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出属性字典清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无属性字典数据，可先登记属性定义</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记属性定义</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <section class="version-panel" data-testid="version-panel">
      <header class="version-head">
        <h3>字段版本与撤回回滚</h3>
        <p class="page-desc">属性定义按生效时间保留版本；撤回会写入回滚版本并生成撤回记录，区分真正撤回的区间与需复核的候选标签。</p>
      </header>

      <form class="filter-bar" @submit.prevent="loadVersionPanel">
        <label class="filter-item">
          <span>属性编号</span>
          <input v-model="versionAttrId" data-testid="version-attr-input" placeholder="如 ATTR-002" required />
        </label>
        <button class="btn primary" data-testid="version-query" type="submit">查询版本</button>
        <button class="btn" data-testid="withdraw-records-btn" type="button" @click="loadWithdraws">查看撤回记录</button>
      </form>

      <div v-if="versionError" class="error-text" data-testid="version-error">{{ versionError }}</div>

      <template v-if="activeAttrId">
        <h4 class="version-subtitle">生效版本时间线（半开区间，同一时点只有一个生效类型）</h4>
        <table class="data-table" data-testid="version-table">
          <thead>
            <tr>
              <th>版本编号</th>
              <th>数据类型</th>
              <th>变更类型</th>
              <th>生效开始</th>
              <th>生效结束</th>
              <th>来源撤回</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ver in versionRows" :key="ver.version_id" :data-testid="`version-row-${ver.version_id}`">
              <td>{{ ver.version_id }}</td>
              <td>{{ ver.data_type }}</td>
              <td>{{ ver.change_type }}</td>
              <td>{{ ver.effective_from }}</td>
              <td>{{ ver.effective_to ?? '至今' }}</td>
              <td>{{ ver.withdraw_id ?? '—' }}</td>
              <td class="row-actions">
                <button
                  v-if="ver.effective_to !== null"
                  class="link"
                  type="button"
                  :data-testid="`rollback-${ver.version_id}`"
                  @click="openWithdraw(ver)"
                >
                  撤回至此版本
                </button>
                <span v-else>当前生效</span>
              </td>
            </tr>
          </tbody>
        </table>

        <form class="filter-bar" @submit.prevent="loadPeriods">
          <label class="filter-item">
            <span>周期粒度</span>
            <select v-model="periodGrain" data-testid="period-grain">
              <option value="day">day</option>
              <option value="week">week</option>
              <option value="month">month</option>
            </select>
          </label>
          <label class="filter-item">
            <span>开始</span>
            <input v-model="periodStart" data-testid="period-start" placeholder="2026-11-14" required />
          </label>
          <label class="filter-item">
            <span>结束</span>
            <input v-model="periodEnd" data-testid="period-end" placeholder="2026-11-19" required />
          </label>
          <button class="btn" data-testid="period-query" type="submit">按周期回查</button>
        </form>
        <table v-if="periodCycles.length" class="data-table" data-testid="period-table">
          <thead>
            <tr>
              <th>周期</th>
              <th>周期起点生效版本</th>
              <th>字段类型</th>
              <th>周期内可用版本数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cycle in periodCycles" :key="cycle.cycle_key" :data-testid="`period-row-${cycle.cycle_key}`">
              <td>{{ cycle.cycle_key }}</td>
              <td>{{ cycle.effective_at_cycle_start ?? '无生效版本' }}</td>
              <td>{{ cycle.effective_data_type ?? '—' }}</td>
              <td>{{ cycle.available_versions.length }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <div v-if="withdrawRows.length" data-testid="withdraw-list">
        <h4 class="version-subtitle">撤回记录（撤回区间与复核候选分开留痕）</h4>
        <article
          v-for="wd in withdrawRows"
          :key="wd.withdraw_id"
          class="withdraw-card"
          :data-testid="`withdraw-card-${wd.withdraw_id}`"
        >
          <header>
            <strong>{{ wd.withdraw_id }}</strong>
            <span>属性 {{ wd.attr_id }} · 生效 {{ wd.effective_time }}</span>
            <span>类型 {{ wd.withdrawn_data_type }} → {{ wd.restored_data_type }}</span>
          </header>
          <p class="withdraw-interval">
            真正撤回区间：
            <template v-for="(iv, idx) in wd.withdrawn_intervals" :key="idx">
              {{ iv.version_id }}（{{ iv.data_type }}）[{{ iv.effective_from }}, {{ iv.effective_to }})
            </template>
          </p>
          <div class="candidate-block">
            <span>需复核候选标签（{{ wd.review_candidate_count }}）：</span>
            <ul>
              <li
                v-for="cand in wd.review_candidates"
                :key="cand.tag_id"
                :data-testid="`candidate-${cand.tag_id}`"
              >
                {{ cand.tag_id }}（{{ cand.tag_name }}）
                <ul>
                  <li v-for="(cf, i) in cand.conflicting_conditions" :key="i" class="conflict-text">
                    {{ cf.conflict_reason }}
                  </li>
                </ul>
              </li>
            </ul>
          </div>
          <p class="unaffected-block">
            旧类型仍支持、无需复核：{{ joinTagIds(wd.unaffected_tags) }}
          </p>
          <button class="btn" type="button" :data-testid="`reopen-${wd.withdraw_id}`" @click="reopen(wd.withdraw_id)">
            重新打开标签校验
          </button>
          <div v-if="reopenReport && reopenReport.withdraw_id === wd.withdraw_id" class="reopen-report" data-testid="reopen-report">
            <p v-for="cand in reopenReport.candidates" :key="cand.tag_id">
              {{ cand.tag_id }} 当前字段类型 {{ cand.current_data_type }}，
              仍冲突操作符：{{ joinOperators(cand.still_conflicting) }}
            </p>
          </div>
        </article>
      </div>
    </section>

    <div v-if="withdrawTarget" class="modal-backdrop" data-testid="withdraw-modal">
      <form class="modal" @submit.prevent="submitWithdraw">
        <h3>撤回当前定义并回滚</h3>
        <p class="page-desc">
          属性 {{ activeAttrId }} 将按生效时间恢复到版本 {{ withdrawTarget.version_id }}
          （字段类型 {{ withdrawTarget.data_type }}），并生成撤回记录。
        </p>
        <label class="create-field">
          <span>撤回生效时间</span>
          <input v-model="withdrawTime" data-testid="withdraw-time" placeholder="2026-11-18T00:00:00" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="withdrawTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-withdraw" type="submit" :disabled="withdrawing">
            {{ withdrawing ? '撤回中…' : '确认撤回' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条属性字典记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type VersionRow = {
  version_id: string
  data_type: string
  change_type: string
  effective_from: string
  effective_to: string | null
  withdraw_id: string | null
}
type WithdrawRecord = {
  withdraw_id: string
  attr_id: string
  effective_time: string
  withdrawn_data_type: string
  restored_data_type: string
  withdrawn_intervals: Array<{ version_id: string; data_type: string; effective_from: string; effective_to: string }>
  review_candidate_count: number
  review_candidates: Array<{
    tag_id: string
    tag_name: string
    conflicting_conditions: Array<{ conflict_reason: string }>
  }>
  unaffected_tags: Array<{ tag_id: string }>
}

type PeriodCycle = {
  cycle_key: string
  effective_at_cycle_start: string | null
  effective_data_type: string | null
  available_versions: Array<{ version_id: string; data_type: string }>
}

type ReopenCandidate = {
  tag_id: string
  current_data_type: string
  still_conflicting: Array<{ operator: string }>
}

type ReopenReport = {
  withdraw_id: string
  candidates: ReopenCandidate[]
}

const ENDPOINT = '/api/attr_dict'
// 后端路由前缀使用连字符，新面板直接对接版本/撤回接口
const VERSION_ENDPOINT = '/api/attr-dict'
const columns = ["属性编号", "属性名称", "数据类型", "枚举值集", "数据来源", "更新频率", "归因方式", "属性版本"]
const actions = ["提交校验", "标记生效", "标记废弃"]
const statuses = ["草稿", "待校验", "已生效", "已废弃"]
const metricNames = ["属性定义总数", "待校验属性", "已生效属性"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'attr_dict.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('属性字典动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '属性字典操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('属性定义列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '属性字典列表读取失败'
  }
}

// ========== 字段版本与撤回回滚 ==========
const versionAttrId = ref('ATTR-002')
const activeAttrId = ref('')
const versionRows = ref<VersionRow[]>([])
const versionError = ref('')
const periodGrain = ref('day')
const periodStart = ref('2026-11-14')
const periodEnd = ref('2026-11-19')
const periodCycles = ref<PeriodCycle[]>([])
const withdrawRows = ref<WithdrawRecord[]>([])
const withdrawTarget = ref<VersionRow | null>(null)
const withdrawTime = ref('')
const withdrawing = ref(false)
const reopenReport = ref<ReopenReport | null>(null)

async function loadVersionPanel() {
  versionError.value = ''
  activeAttrId.value = versionAttrId.value.trim()
  periodCycles.value = []
  try {
    const response = await request(`${VERSION_ENDPOINT}/versions/${encodeURIComponent(activeAttrId.value)}`)
    if (!response.ok) throw new Error('版本时间线读取失败')
    const payload = await response.json()
    versionRows.value = payload.items ?? []
  } catch (error) {
    versionRows.value = []
    versionError.value = error instanceof Error ? error.message : '版本时间线读取失败'
  }
  await loadWithdraws()
}

async function loadPeriods() {
  versionError.value = ''
  try {
    const params = new URLSearchParams({
      period_grain: periodGrain.value,
      start: periodStart.value,
      end: periodEnd.value,
    })
    const response = await request(
      `${VERSION_ENDPOINT}/versions/${encodeURIComponent(activeAttrId.value)}/periods?${params}`,
    )
    if (!response.ok) throw new Error('周期版本回查失败')
    const payload = await response.json()
    periodCycles.value = payload.cycles ?? []
  } catch (error) {
    periodCycles.value = []
    versionError.value = error instanceof Error ? error.message : '周期版本回查失败'
  }
}

async function loadWithdraws() {
  versionError.value = ''
  try {
    const attrFilter = activeAttrId.value ? `?attr_id=${encodeURIComponent(activeAttrId.value)}` : ''
    const response = await request(`${VERSION_ENDPOINT}/withdraws${attrFilter}`)
    if (!response.ok) throw new Error('撤回记录读取失败')
    const payload = await response.json()
    withdrawRows.value = payload.items ?? []
  } catch (error) {
    versionError.value = error instanceof Error ? error.message : '撤回记录读取失败'
  }
}

function openWithdraw(version: VersionRow) {
  withdrawTarget.value = version
  withdrawTime.value = new Date().toISOString().slice(0, 19)
  reopenReport.value = null
}

async function submitWithdraw() {
  if (!withdrawTarget.value) return
  withdrawing.value = true
  versionError.value = ''
  try {
    const response = await request(
      `${VERSION_ENDPOINT}/withdraws/${encodeURIComponent(activeAttrId.value)}`,
      {
        method: 'POST',
        body: JSON.stringify({
          target_version_id: withdrawTarget.value.version_id,
          effective_time: withdrawTime.value,
        }),
      },
    )
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.detail || payload.message || '撤回失败')
    withdrawTarget.value = null
    await loadVersionPanel()
    await reload()
  } catch (error) {
    versionError.value = error instanceof Error ? error.message : '撤回失败'
  } finally {
    withdrawing.value = false
  }
}

async function reopen(withdrawId: string) {
  versionError.value = ''
  try {
    const response = await request(
      `${VERSION_ENDPOINT}/withdraws/${encodeURIComponent(withdrawId)}/reopen-validation`,
      { method: 'POST' },
    )
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error('重新校验失败')
    reopenReport.value = payload.entry
  } catch (error) {
    versionError.value = error instanceof Error ? error.message : '重新校验失败'
  }
}

function joinTagIds(tags: Array<{ tag_id: string }>): string {
  return tags.map((t) => t.tag_id).join('、')
}

function joinOperators(conflicts: Array<{ operator: string }>): string {
  const ops = conflicts.map((c) => c.operator)
  return ops.length ? ops.join('、') : '无'
}

onMounted(() => {
  void reload()
  void loadVersionPanel()
})
</script>

<style scoped>
.version-panel {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--border-color, #d9d9d9);
  border-radius: 8px;
  background: var(--panel-bg, #fafafa);
}

.version-head h3 {
  margin: 0 0 4px;
}

.version-subtitle {
  margin: 18px 0 8px;
}

.withdraw-card {
  margin-top: 12px;
  padding: 12px 16px;
  border: 1px solid var(--border-color, #d9d9d9);
  border-radius: 8px;
  background: var(--card-bg, #fff);
}

.withdraw-card header {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.withdraw-interval,
.unaffected-block {
  margin: 6px 0;
  font-size: 13px;
}

.candidate-block ul {
  margin: 4px 0 0;
  padding-left: 20px;
}

.conflict-text {
  color: var(--danger-color, #cf1322);
  font-size: 13px;
}

.reopen-report {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--report-bg, #f0f5ff);
  font-size: 13px;
}
</style>
