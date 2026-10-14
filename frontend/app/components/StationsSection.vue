<template>
  <section class="stations-section">
    <div class="section-header">
      <h2 class="section-title">{{ t('stations.title') }}</h2>
      <button class="btn btn--sm btn--ghost" @click="filesOpen = !filesOpen">
        {{ t('firmware.filesTitle') }} ({{ files.length }})
      </button>
    </div>

    <!-- Firmware files: upload, list, delete -->
    <div v-if="filesOpen" class="fw-panel">
      <div class="fw-panel__upload">
        <input
          v-model="note"
          class="form-input"
          type="text"
          maxlength="200"
          :placeholder="t('firmware.notePlaceholder')"
          :aria-label="t('firmware.note')"
          :disabled="uploadPercent !== null"
        >
        <label class="btn btn--primary btn--sm" :class="{ 'is-disabled': uploadPercent !== null }">
          {{ uploadPercent !== null ? t('firmware.uploading', { percent: uploadPercent }) : t('firmware.upload') }}
          <input type="file" class="sr-only" :disabled="uploadPercent !== null" @change="onPickFile">
        </label>
      </div>
      <p v-if="panelError" class="fw-error" role="alert">{{ panelError }}</p>

      <div v-if="files.length === 0" class="muted small">{{ t('firmware.noFiles') }}</div>
      <div v-else class="fw-files">
        <div v-for="f in files" :key="f.id" class="fw-file">
          <div class="fw-file__main">
            <span class="fw-file__name">{{ f.filename }}</span>
            <span class="muted small">{{ formatSize(f.size_bytes) }} · {{ formatDate(f.uploaded_at) }}</span>
            <span v-if="f.note" class="muted small">{{ f.note }}</span>
            <span class="fw-file__hash mono" :title="f.sha256">{{ t('firmware.sha256') }}: {{ f.sha256 }}</span>
          </div>
          <div class="fw-file__actions">
            <button class="btn btn--sm btn--ghost" @click="copyHash(f)">
              {{ copiedId === f.id ? t('firmware.copied') : t('firmware.copy') }}
            </button>
            <button class="btn btn--sm btn--danger" @click="$emit('deleteFile', f)">{{ t('firmware.delete') }}</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loading && stations.length === 0" class="loading-hint">{{ t('stations.loading') }}</div>

    <!-- Empty state -->
    <div v-else-if="stations.length === 0" class="empty-state">
      <div class="empty-state__icon">—</div>
      <div class="empty-state__text">{{ t('stations.empty') }}</div>
      <div class="form-hint">{{ t('stations.emptyHint') }}</div>
    </div>

    <div v-else class="stations-list">
      <article v-for="s in stations" :key="s.id" class="station-card">

        <!-- Header: name, id, online badge, firmware -->
        <header class="station-card__head">
          <div class="station-card__title">
            <span class="station-card__name">{{ s.model || s.id }}</span>
            <span class="station-card__id mono muted">{{ s.id }}</span>
          </div>
          <div class="station-card__badges">
            <span v-if="s.firmware" class="fw-badge mono" :title="t('stations.firmware')">FW {{ s.firmware }}</span>
            <span class="status" :class="s.online ? 'status--active' : 'status--done'">
              {{ s.online ? t('stations.online') : t('stations.offline') }}
            </span>
          </div>
        </header>

        <!-- Firmware update -->
        <div class="station-card__section">
          <span class="station-card__label">{{ t('stations.firmware') }}</span>
          <div class="fw-row">
            <span class="mono">{{ s.firmware || '–' }}</span>
            <span v-if="s.firmware_update" class="chip" :class="updateClass(s.firmware_update.status)">
              {{ updateLabel(s.firmware_update.status) }}
            </span>
            <span v-if="s.firmware_update" class="muted small">
              {{ s.firmware_update.filename }}<template v-if="s.firmware_update.to_version"> · {{ t('firmware.versionChange', { from: s.firmware_update.from_version || '–', to: s.firmware_update.to_version }) }}</template>
            </span>
            <button
              class="btn btn--sm btn--ghost fw-row__btn"
              :disabled="!!blockReason(s)"
              :title="blockReason(s)"
              @click="$emit('updateFirmware', s)"
            >{{ t('firmware.update') }}</button>
          </div>
          <div v-if="s.firmware_update?.detail" class="fw-detail">{{ s.firmware_update.detail }}</div>
        </div>

        <!-- Connectors -->
        <div class="station-card__section">
          <span class="station-card__label">{{ t('stations.connectors') }}</span>
          <div v-if="s.connectors.length === 0" class="muted small">{{ t('stations.noConnectors') }}</div>
          <div v-else class="connector-list">
            <div v-for="c in s.connectors" :key="c.connector_id" class="connector">
              <div class="connector__row">
                <span class="connector__name">
                  {{ c.connector_id === 0 ? t('stations.station') : t('stations.connector', { id: c.connector_id }) }}
                </span>
                <span class="chip" :class="statusClass(c.status)">{{ statusLabel(c.status) }}</span>
                <span v-if="c.status_timestamp || c.updated_at" class="muted small">
                  {{ t('stations.since') }} {{ formatDate(c.status_timestamp || c.updated_at) }}
                </span>
              </div>
              <div v-if="hasError(c)" class="connector__error">
                {{ t('stations.error') }}: {{ c.error_code }}<template v-if="c.vendor_error_code"> ({{ c.vendor_error_code }})</template><template v-if="c.info"> – {{ c.info }}</template>
              </div>
              <div v-if="c.active_session" class="connector__live mono">
                <span v-if="c.active_session.vehicle_name">{{ c.active_session.vehicle_name }} · </span>
                <span>{{ formatDuration(c.active_session.duration_seconds) }}</span>
                <span v-if="c.active_session.session_energy_kwh != null">
                  · {{ c.active_session.session_energy_kwh.toLocaleString(numLocale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }} kWh
                </span>
                <span v-if="powerOf(c)"> · {{ powerOf(c) }} kW</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Connection + identity -->
        <div class="station-card__grid">
          <div class="kv">
            <span class="kv__k">{{ t('stations.lastSeen') }}</span>
            <span class="kv__v">{{ ago(s.last_seen_seconds) }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.connectedSince') }}</span>
            <span class="kv__v mono">{{ formatDate(s.connected_at) }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.remoteAddress') }}</span>
            <span class="kv__v mono">{{ s.remote_address || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.heartbeat') }}</span>
            <span class="kv__v mono">{{ s.heartbeat_interval != null ? s.heartbeat_interval + ' s' : '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.vendor') }}</span>
            <span class="kv__v">{{ s.vendor || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.model') }}</span>
            <span class="kv__v">{{ s.model || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.serial') }}</span>
            <span class="kv__v mono">{{ s.serial_number || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.boxSerial') }}</span>
            <span class="kv__v mono">{{ s.box_serial_number || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.meterSerial') }}</span>
            <span class="kv__v mono">{{ s.meter_serial_number || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.meterType') }}</span>
            <span class="kv__v">{{ s.meter_type || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.iccid') }}</span>
            <span class="kv__v mono">{{ s.iccid || '–' }}</span>
          </div>
          <div class="kv">
            <span class="kv__k">{{ t('stations.imsi') }}</span>
            <span class="kv__v mono">{{ s.imsi || '–' }}</span>
          </div>
        </div>

        <!-- Usage statistics -->
        <div class="station-card__section">
          <span class="station-card__label">{{ t('stations.usage') }}</span>
          <div class="stats-row">
            <div class="stat">
              <span class="stat__v">{{ s.stats.active_sessions }}</span>
              <span class="stat__k">{{ t('stations.activeNow') }}</span>
            </div>
            <div class="stat">
              <span class="stat__v">{{ s.stats.sessions_30d }}</span>
              <span class="stat__k">{{ t('stations.sessions30d') }}</span>
            </div>
            <div class="stat">
              <span class="stat__v">{{ kwh(s.stats.kwh_30d) }}</span>
              <span class="stat__k">{{ t('stations.energy30d') }}</span>
            </div>
            <div class="stat">
              <span class="stat__v">{{ kwh(s.stats.kwh_total) }}</span>
              <span class="stat__k">{{ t('stations.energyTotal') }}</span>
            </div>
            <div class="stat">
              <span class="stat__v stat__v--date">{{ formatDate(s.stats.last_session_start) }}</span>
              <span class="stat__k">{{ t('stations.lastSession') }}</span>
            </div>
          </div>
        </div>

      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { FirmwareFile, Station, StationConnector } from '~/types'

defineProps<{
  loading: boolean
  stations: Station[]
  files: FirmwareFile[]
  uploadPercent: number | null
  panelError: string
}>()

const emit = defineEmits<{
  updateFirmware: [station: Station]
  uploadFile: [payload: { file: File; note: string }]
  deleteFile: [file: FirmwareFile]
}>()

const { t, numLocale } = useLocale()
const { formatDate, formatDuration, formatPower } = useFormatters()

const filesOpen = ref(false)
const note = ref('')
const copiedId = ref<number | null>(null)

// Statuses after which no further change is expected (mirrors the backend).
const TERMINAL = new Set(['Installed', 'DownloadFailed', 'InstallationFailed', 'failed'])

function onPickFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    emit('uploadFile', { file, note: note.value.trim() })
    note.value = ''
  }
  input.value = ''   // allow picking the same file again
}

async function copyHash(f: FirmwareFile) {
  try {
    await navigator.clipboard.writeText(f.sha256)
    copiedId.value = f.id
    setTimeout(() => { if (copiedId.value === f.id) copiedId.value = null }, 1500)
  } catch { /* clipboard unavailable (e.g. plain HTTP) – the hash is selectable text */ }
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} kB`
  return `${bytes} B`
}

const KNOWN_UPDATE_STATUS = new Set([
  'queued', 'sending', 'sent', 'Downloading', 'Downloaded', 'Installing',
  'Installed', 'DownloadFailed', 'InstallationFailed', 'failed',
])

function updateLabel(status: string): string {
  return KNOWN_UPDATE_STATUS.has(status) ? t(`firmware.status.${status}`) : status
}

function updateClass(status: string): string {
  if (status === 'Installed') return 'chip--ok'
  if (status === 'failed' || status === 'DownloadFailed' || status === 'InstallationFailed') return 'chip--fault'
  return 'chip--warn'
}

/** Why the Update button is disabled, or '' when an update can be started. */
function blockReason(s: Station): string {
  if (s.firmware_update && !TERMINAL.has(s.firmware_update.status)) return t('firmware.disabledRunning')
  if (!s.online) return t('firmware.disabledOffline')
  if (s.stats.active_sessions > 0) return t('firmware.disabledCharging')
  return ''
}

// OCPP 1.6 ChargePointStatus values that have a translation and a colour.
const KNOWN_STATUS = new Set([
  'Available', 'Preparing', 'Charging', 'SuspendedEVSE', 'SuspendedEV',
  'Finishing', 'Reserved', 'Unavailable', 'Faulted',
])

function statusLabel(status: string | null): string {
  if (!status) return '–'
  return KNOWN_STATUS.has(status) ? t(`stations.status.${status}`) : status
}

function statusClass(status: string | null): string {
  switch (status) {
    case 'Charging':                          return 'chip--charging'
    case 'Available':                         return 'chip--ok'
    case 'Preparing':
    case 'Finishing':
    case 'Reserved':
    case 'SuspendedEV':
    case 'SuspendedEVSE':                     return 'chip--warn'
    case 'Faulted':                           return 'chip--fault'
    default:                                  return 'chip--idle'
  }
}

function hasError(c: StationConnector): boolean {
  return !!c.error_code && c.error_code !== 'NoError'
}

function powerOf(c: StationConnector): string {
  const m = c.active_session?.latest_meter_values.find(v => v.measurand === 'Power.Active.Import')
  return m ? formatPower(m) : ''
}

function kwh(v: number): string {
  return `${v.toLocaleString(numLocale.value, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} kWh`
}

function ago(secs: number | null): string {
  if (secs == null) return '–'
  if (secs < 60)    return t('stations.secondsAgo', { n: Math.max(0, secs) })
  if (secs < 3600)  return t('stations.minutesAgo', { n: Math.floor(secs / 60) })
  if (secs < 86400) return t('stations.hoursAgo',   { n: Math.floor(secs / 3600) })
  return t('stations.daysAgo', { n: Math.floor(secs / 86400) })
}
</script>

<style scoped>
.stations-section {
  padding: 1rem 0.9rem;
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
}
@media (min-width: 640px) {
  .stations-section { padding: 1.75rem 2rem; }
}

.stations-list { display: flex; flex-direction: column; gap: 0.875rem; }

.station-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.125rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  box-shadow: var(--shadow);
}

.station-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.station-card__title { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.station-card__name { font-weight: 700; font-size: 1rem; color: var(--text); word-break: break-word; }
.station-card__id { font-size: 0.78rem; word-break: break-all; }
.station-card__badges { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }

.fw-badge {
  font-size: 0.72rem;
  padding: 3px 10px;
  border-radius: 99px;
  background: var(--bg-card-2);
  border: 1px solid var(--border);
  color: var(--text-muted);
  word-break: break-all;
}

/* Firmware */
.fw-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.fw-panel__upload { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.fw-panel__upload .form-input { flex: 1 1 220px; }
.is-disabled { opacity: 0.5; pointer-events: none; }
.fw-error { color: var(--danger); font-size: 0.85rem; font-weight: 600; }
.fw-files { display: flex; flex-direction: column; gap: 0.5rem; }
.fw-file {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  background: var(--bg-card-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.6rem 0.8rem;
}
.fw-file__main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 260px; }
.fw-file__name { font-weight: 600; font-size: 0.875rem; word-break: break-all; }
.fw-file__hash { font-size: 0.7rem; color: var(--text-muted); word-break: break-all; }
.fw-file__actions { display: flex; gap: 0.4rem; align-items: flex-start; }
.fw-row { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.fw-row__btn { margin-left: auto; }
.fw-detail { font-size: 0.8rem; color: var(--danger); word-break: break-word; }

.station-card__section { display: flex; flex-direction: column; gap: 0.5rem; }
.station-card__label {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--text-muted);
  font-weight: 600;
}
.small { font-size: 0.8rem; }

/* Connectors */
.connector-list { display: flex; flex-direction: column; gap: 0.5rem; }
.connector {
  background: var(--bg-card-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.6rem 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.connector__row { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.connector__name { font-weight: 600; font-size: 0.875rem; }
.connector__error { font-size: 0.8rem; color: var(--danger); word-break: break-word; }
.connector__live { font-size: 0.8rem; color: var(--text-muted); }

.chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 99px;
  font-size: 0.72rem;
  font-weight: 700;
  border: 1px solid var(--border);
}
.chip--ok       { background: var(--accent-dim); color: var(--accent); border-color: rgba(22,163,74,0.25); }
.chip--charging { background: var(--accent); color: #fff; border-color: var(--accent); }
.chip--warn     { background: #fffbeb; color: #b45309; border-color: #fcd34d; }
.chip--fault    { background: var(--danger-dim); color: var(--danger); border-color: rgba(197,48,48,0.3); }
.chip--idle     { background: var(--bg-card-2); color: var(--text-muted); }

/* Key / value grid */
.station-card__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.75rem 1rem;
}
.kv { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.kv__k {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-muted);
}
.kv__v { font-size: 0.875rem; color: var(--text); word-break: break-all; }

/* Usage */
.stats-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}
@media (min-width: 640px) {
  .stats-row { grid-template-columns: repeat(5, minmax(0, 1fr)); }
}
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat__v { font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat__v--date { font-size: 0.85rem; font-weight: 500; }
.stat__k { font-size: 0.7rem; color: var(--text-muted); }
</style>
