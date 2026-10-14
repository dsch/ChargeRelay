<template>
  <section class="cfg-section">
    <div class="section-header">
      <button class="btn btn--sm btn--ghost" @click="$emit('back')">← {{ t('stationConfig.back') }}</button>
    </div>

    <!-- Header -->
    <div class="cfg-head">
      <div class="cfg-head__title">
        <span class="cfg-head__name">{{ station.model || station.id }}</span>
        <span class="mono muted small">{{ station.id }}</span>
      </div>
      <div class="cfg-head__badges">
        <span v-if="station.firmware" class="fw-badge mono">FW {{ station.firmware }}</span>
        <span class="status" :class="station.online ? 'status--active' : 'status--done'">
          {{ station.online ? t('stations.online') : t('stations.offline') }}
        </span>
      </div>
    </div>

    <p v-if="!station.online" class="muted small">{{ t('stationConfig.offlineHint') }}</p>

    <!-- Feedback for the last action -->
    <p v-if="notice" class="cfg-notice" :class="`cfg-notice--${notice.kind}`" role="status">{{ notice.text }}</p>
    <div v-if="rebootNeeded" class="cfg-reboot" role="alert">
      <span>{{ t('stationConfig.rebootBanner') }}</span>
      <button class="btn btn--sm btn--danger" :disabled="busy || !station.online" @click="confirmReset('Soft')">
        {{ t('stationConfig.resetNow') }}
      </button>
    </div>

    <!-- Configuration table -->
    <div class="cfg-card">
      <div class="cfg-card__bar">
        <h3 class="cfg-card__title">{{ t('stationConfig.title') }}</h3>
        <span class="muted small">
          {{ config?.fetched_at ? t('stationConfig.fetchedAt', { time: formatDate(config.fetched_at) }) : t('stationConfig.neverFetched') }}
        </span>
        <button class="btn btn--sm btn--primary cfg-card__refresh" :disabled="busy || !station.online" @click="$emit('refresh')">
          {{ reading ? t('stationConfig.refreshing') : t('stationConfig.refresh') }}
        </button>
      </div>

      <input
        v-model="search"
        class="form-input"
        type="search"
        :placeholder="t('stationConfig.search')"
        :aria-label="t('stationConfig.search')"
      >

      <div v-if="!config || config.items.length === 0" class="muted small">{{ t('stationConfig.noKeys') }}</div>
      <div v-else-if="filtered.length === 0" class="muted small">{{ t('stationConfig.noMatch') }}</div>
      <div v-else class="cfg-rows">
        <div v-for="item in filtered" :key="item.key" class="cfg-row">
          <div class="cfg-row__key">
            <span class="mono">{{ item.key }}</span>
            <span v-if="item.readonly" class="chip chip--idle">{{ t('stationConfig.readonly') }}</span>
          </div>

          <!-- Editing -->
          <form v-if="editingKey === item.key" class="cfg-row__edit" @submit.prevent="save(item)">
            <input
              v-model="draft"
              class="form-input mono"
              type="text"
              maxlength="500"
              :placeholder="item.masked ? t('stationConfig.hiddenHint') : ''"
              :aria-label="item.key"
              autocomplete="off"
            >
            <button class="btn btn--sm btn--primary" type="submit" :disabled="busy">{{ t('stationConfig.save') }}</button>
            <button class="btn btn--sm btn--ghost" type="button" :disabled="busy" @click="editingKey = null">
              {{ t('stationConfig.cancel') }}
            </button>
          </form>

          <!-- Display -->
          <div v-else class="cfg-row__value">
            <span v-if="item.masked" class="muted" :title="t('stationConfig.hiddenHint')">••• {{ t('stationConfig.hidden') }}</span>
            <span v-else class="mono cfg-row__text">{{ item.value ?? '–' }}</span>
            <span v-if="!item.readonly && item.key.length > MAX_EDIT_KEY" class="muted small" :title="t('stationConfig.errorKeyTooLong')">
              {{ t('stationConfig.tooLong') }}
            </span>
            <button
              v-else-if="!item.readonly"
              class="btn btn--sm btn--ghost"
              :disabled="busy || !station.online"
              @click="startEdit(item)"
            >{{ t('stationConfig.edit') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Actions -->
    <div class="cfg-card">
      <h3 class="cfg-card__title">{{ t('stationConfig.actions') }}</h3>

      <div class="cfg-action">
        <span class="cfg-action__label">{{ t('stationConfig.reset') }}</span>
        <select v-model="resetType" class="form-input cfg-action__select" :aria-label="t('stationConfig.resetType')">
          <option value="Soft">{{ t('stationConfig.resetSoft') }}</option>
          <option value="Hard">{{ t('stationConfig.resetHard') }}</option>
        </select>
        <button class="btn btn--sm btn--danger" :disabled="busy || !station.online" @click="confirmReset(resetType)">
          {{ t('stationConfig.resetBtn') }}
        </button>
      </div>

      <div class="cfg-action">
        <span class="cfg-action__label">{{ t('stationConfig.trigger') }}</span>
        <select v-model="triggerMessage" class="form-input cfg-action__select" :aria-label="t('stationConfig.triggerMessage')">
          <option v-for="m in TRIGGER_MESSAGES" :key="m" :value="m">{{ m }}</option>
        </select>
        <input
          v-model="triggerConnector"
          class="form-input cfg-action__connector"
          type="number"
          min="0"
          max="1000"
          :placeholder="t('stationConfig.triggerConnector')"
          :aria-label="t('stationConfig.triggerConnector')"
        >
        <button class="btn btn--sm btn--ghost" :disabled="busy || !station.online" @click="sendTrigger">
          {{ t('stationConfig.triggerBtn') }}
        </button>
      </div>
    </div>

    <!-- History -->
    <div class="cfg-card">
      <h3 class="cfg-card__title">{{ t('stationConfig.history') }}</h3>
      <div v-if="history.length === 0" class="muted small">{{ t('stationConfig.noHistory') }}</div>
      <div v-else class="cfg-history">
        <div v-for="c in history" :key="c.id" class="cfg-history__row">
          <span class="cfg-history__cmd">{{ commandLabel(c) }}</span>
          <span class="chip" :class="chipClass(c)">{{ historyStatus(c) }}</span>
          <span class="muted small">{{ formatDate(c.requested_at) }}</span>
          <span v-if="c.detail" class="cfg-history__detail">{{ c.detail }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { ConfigItem, Station, StationCommand, StationConfig } from '~/types'

const props = defineProps<{
  station: Station
  config: StationConfig | null
  history: StationCommand[]
  busy: boolean
  reading: boolean
  rebootNeeded: boolean
  notice: { kind: 'ok' | 'error'; text: string } | null
}>()

const emit = defineEmits<{
  back: []
  refresh: []
  changeKey: [payload: { key: string; value: string }]
  reset: [type: 'Soft' | 'Hard']
  trigger: [payload: { message: string; connectorId: number | null }]
}>()

const { t } = useLocale()
const { formatDate } = useFormatters()

// OCPP 1.6 messages the station can be asked to resend (mirrors the API).
const TRIGGER_MESSAGES = [
  'BootNotification', 'StatusNotification', 'MeterValues', 'Heartbeat',
  'FirmwareStatusNotification', 'DiagnosticsStatusNotification',
]

// OCPP limits ChangeConfiguration keys to 50 characters (the ocpp library refuses longer ones).
const MAX_EDIT_KEY = 50

const search = ref('')
const editingKey = ref<string | null>(null)
const draft = ref('')
const resetType = ref<'Soft' | 'Hard'>('Soft')
const triggerMessage = ref(TRIGGER_MESSAGES[0])
const triggerConnector = ref('')

const filtered = computed(() => {
  const items = props.config?.items ?? []
  const q = search.value.trim().toLowerCase()
  return q ? items.filter(i => i.key.toLowerCase().includes(q)) : items
})

function startEdit(item: ConfigItem) {
  editingKey.value = item.key
  // Secret values are never sent to the browser, so start empty.
  draft.value = item.masked ? '' : (item.value ?? '')
}

function save(item: ConfigItem) {
  if (!confirm(t('stationConfig.changeWarning'))) return
  emit('changeKey', { key: item.key, value: draft.value })
  editingKey.value = null
}

function confirmReset(type: 'Soft' | 'Hard') {
  if (confirm(t('stationConfig.resetConfirm'))) emit('reset', type)
}

function sendTrigger() {
  const raw = triggerConnector.value.toString().trim()
  const id = raw === '' ? null : Number(raw)
  if (id !== null && (!Number.isInteger(id) || id < 0)) return
  emit('trigger', { message: triggerMessage.value ?? 'StatusNotification', connectorId: id })
}

function commandLabel(c: StationCommand): string {
  switch (c.command) {
    case 'ChangeConfiguration':
      return t('stationConfig.cmd.ChangeConfiguration', { key: String(c.params?.key ?? '') })
    case 'Reset':
      return t('stationConfig.cmd.Reset', { type: String(c.params?.type ?? '') })
    case 'TriggerMessage':
      return t('stationConfig.cmd.TriggerMessage', { message: String(c.params?.message ?? '') })
    default:
      return t('stationConfig.cmd.GetConfiguration')
  }
}

const KNOWN_RESULTS = new Set(['Accepted', 'Rejected', 'RebootRequired', 'NotSupported', 'NotImplemented'])

function historyStatus(c: StationCommand): string {
  if (c.status === 'done' && c.result?.status) {
    return KNOWN_RESULTS.has(c.result.status) ? t(`stationConfig.result.${c.result.status}`) : c.result.status
  }
  return t(`stationConfig.status.${c.status}`)
}

function chipClass(c: StationCommand): string {
  if (c.status === 'failed') return 'chip--fault'
  if (c.status !== 'done') return 'chip--warn'
  const r = c.result?.status
  if (r === 'Rejected' || r === 'NotSupported' || r === 'NotImplemented') return 'chip--fault'
  if (r === 'RebootRequired') return 'chip--warn'
  return 'chip--ok'
}
</script>

<style scoped>
.cfg-section {
  padding: 1rem 0.9rem;
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}
@media (min-width: 640px) {
  .cfg-section { padding: 1.75rem 2rem; }
}
.cfg-section .section-header { margin-bottom: 0; }

.cfg-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.75rem; flex-wrap: wrap; }
.cfg-head__title { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cfg-head__name { font-weight: 700; font-size: 1.1rem; word-break: break-word; }
.cfg-head__badges { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.small { font-size: 0.8rem; }

.fw-badge {
  font-size: 0.72rem;
  padding: 3px 10px;
  border-radius: 99px;
  background: var(--bg-card-2);
  border: 1px solid var(--border);
  color: var(--text-muted);
  word-break: break-all;
}

.cfg-notice { font-size: 0.85rem; font-weight: 600; padding: 0.55rem 0.85rem; border-radius: var(--radius-sm); }
.cfg-notice--ok { background: var(--accent-dim); color: var(--accent-dark); }
.cfg-notice--error { background: var(--danger-dim); color: var(--danger); }
.cfg-reboot {
  display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; flex-wrap: wrap;
  background: #fffbeb; border: 1px solid #fcd34d; color: #b45309;
  border-radius: var(--radius-sm); padding: 0.6rem 0.85rem; font-size: 0.85rem; font-weight: 600;
}

.cfg-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  box-shadow: var(--shadow);
}
.cfg-card__bar { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.cfg-card__title { font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); }
.cfg-card__refresh { margin-left: auto; }

.cfg-rows { display: flex; flex-direction: column; }
.cfg-row {
  display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;
  padding: 0.55rem 0; border-bottom: 1px solid var(--border-soft);
}
.cfg-row:last-child { border-bottom: none; }
.cfg-row__key { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; min-width: 0; flex: 1 1 220px; font-size: 0.85rem; word-break: break-all; }
.cfg-row__value, .cfg-row__edit { display: flex; align-items: center; gap: 0.5rem; flex: 1 1 260px; justify-content: flex-end; min-width: 0; }
.cfg-row__edit .form-input { flex: 1; min-width: 0; }
.cfg-row__text { font-size: 0.85rem; word-break: break-all; text-align: right; }

.chip { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 0.72rem; font-weight: 700; border: 1px solid var(--border); }
.chip--ok   { background: var(--accent-dim); color: var(--accent); border-color: rgba(22,163,74,0.25); }
.chip--warn { background: #fffbeb; color: #b45309; border-color: #fcd34d; }
.chip--fault { background: var(--danger-dim); color: var(--danger); border-color: rgba(197,48,48,0.3); }
.chip--idle { background: var(--bg-card-2); color: var(--text-muted); }

.cfg-action { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.cfg-action__label { font-size: 0.85rem; font-weight: 600; min-width: 130px; }
.cfg-action__select { flex: 0 1 260px; }
.cfg-action__connector { flex: 0 1 160px; }

.cfg-history { display: flex; flex-direction: column; gap: 0.4rem; }
.cfg-history__row { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; font-size: 0.85rem; }
.cfg-history__cmd { font-weight: 600; word-break: break-all; }
.cfg-history__detail { color: var(--danger); font-size: 0.8rem; }
</style>
