<template>
  <div class="modal-overlay" @click.self="!saving && $emit('close')">
    <div class="modal">
      <div class="modal__header">
        <h3 class="modal__title">{{ t('firmware.update') }}</h3>
        <button class="modal__close" :disabled="saving" @click="$emit('close')">✕</button>
      </div>

      <form class="modal__body" @submit.prevent="submit">
        <!-- Station summary -->
        <div class="fw-summary">
          <div class="fw-summary__row">
            <span class="fw-summary__k">{{ t('stations.title') }}</span>
            <span class="fw-summary__v">{{ station.model || station.id }} <span class="mono muted">{{ station.id }}</span></span>
          </div>
          <div class="fw-summary__row">
            <span class="fw-summary__k">{{ t('firmware.current') }}</span>
            <span class="fw-summary__v mono">{{ station.firmware || '–' }}</span>
          </div>
        </div>

        <!-- File -->
        <div class="form-group">
          <label class="form-label" for="fw-file">{{ t('firmware.file') }} <span class="required">*</span></label>
          <select id="fw-file" v-model="fileId" class="form-input" :disabled="saving">
            <option :value="null">{{ t('firmware.selectFile') }}</option>
            <option v-for="f in files" :key="f.id" :value="f.id">
              {{ f.filename }} ({{ formatSize(f.size_bytes) }})
            </option>
          </select>
          <div v-if="selected" class="fw-hash mono">
            {{ t('firmware.sha256') }}: {{ selected.sha256 }}
          </div>
          <div v-if="selected?.note" class="form-hint">{{ selected.note }}</div>
        </div>

        <div class="fw-warning" role="alert">{{ t('firmware.warning') }}</div>

        <p v-if="error" class="fw-error" role="alert">{{ error }}</p>
      </form>

      <div class="modal__footer">
        <button type="button" class="btn btn--ghost" :disabled="saving" @click="$emit('close')">{{ t('firmware.cancel') }}</button>
        <button type="button" class="btn btn--danger" :disabled="!canSubmit || saving" @click="submit">
          {{ saving ? t('firmware.starting') : t('firmware.start') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { FirmwareFile, Station } from '~/types'

const props = defineProps<{
  station: Station
  files: FirmwareFile[]
  saving: boolean
  error: string
}>()

const emit = defineEmits<{
  close: []
  start: [data: { fileId: number }]
}>()

const { t } = useLocale()

const fileId = ref<number | null>(props.files[0]?.id ?? null)

const selected = computed(() => props.files.find(f => f.id === fileId.value) ?? null)
const canSubmit = computed(() => fileId.value !== null)

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} kB`
  return `${bytes} B`
}

function submit() {
  if (!canSubmit.value || props.saving || fileId.value === null) return
  emit('start', { fileId: fileId.value })
}
</script>

<style scoped>
.fw-summary {
  background: var(--bg-card-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.fw-summary__row { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
.fw-summary__k { font-size: 0.75rem; color: var(--text-muted); flex-shrink: 0; }
.fw-summary__v { font-size: 0.875rem; text-align: right; word-break: break-all; }
.fw-hash { font-size: 0.72rem; color: var(--text-muted); word-break: break-all; }
.fw-warning {
  background: var(--danger-dim);
  border: 1px solid rgba(197, 48, 48, 0.25);
  color: var(--danger);
  border-radius: var(--radius-sm);
  padding: 0.65rem 0.875rem;
  font-size: 0.8rem;
  line-height: 1.4;
}
.fw-error { color: var(--danger); font-size: 0.85rem; font-weight: 600; }
</style>
