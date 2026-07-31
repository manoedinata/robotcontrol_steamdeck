<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowBigUp, Check, Delete, Trash2, X } from '@lucide/vue'

const props = defineProps({
  layout: {
    type: String,
    default: 'text',
  },
  maxLength: {
    type: Number,
    default: 128,
  },
  title: {
    type: String,
    required: true,
  },
  value: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['cancel', 'done'])

const keyboard = ref(null)
const draft = ref(props.value)
const shifted = ref(false)

const letterRows = [
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm'],
]

const rows = computed(() => {
  if (props.layout === 'ip') {
    return [
      ['1', '2', '3'],
      ['4', '5', '6'],
      ['7', '8', '9'],
      ['.', '0'],
    ]
  }

  if (props.layout === 'integer') {
    return [
      ['1', '2', '3'],
      ['4', '5', '6'],
      ['7', '8', '9'],
      ['0'],
    ]
  }

  if (props.layout === 'decimal') {
    return [
      ['1', '2', '3'],
      ['4', '5', '6'],
      ['7', '8', '9'],
      ['.', '0'],
    ]
  }

  if (props.layout === 'hostname') {
    return [
      ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      ...letterRows,
    ]
  }

  return letterRows
})

const isTextLayout = computed(() => ['text', 'hostname'].includes(props.layout))

function appendKey(key) {
  if (draft.value.length >= props.maxLength) return
  const nextKey = shifted.value && /^[a-z]$/.test(key) ? key.toUpperCase() : key
  draft.value += nextKey
}

function backspace() {
  draft.value = draft.value.slice(0, -1)
}

function clear() {
  draft.value = ''
}

function finish() {
  emit('done', draft.value)
}

function handleWindowKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
  }
}

onMounted(async () => {
  window.addEventListener('keydown', handleWindowKeydown)
  await nextTick()
  keyboard.value?.querySelector('button')?.focus()
})

onBeforeUnmount(() => window.removeEventListener('keydown', handleWindowKeydown))
</script>

<template>
  <div class="osk-backdrop" role="presentation" @pointerdown.self="emit('cancel')">
    <section
      ref="keyboard"
      class="osk-panel"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="`${title.replaceAll(' ', '-').toLowerCase()}-keyboard-title`"
    >
      <header class="osk-header">
        <h2 :id="`${title.replaceAll(' ', '-').toLowerCase()}-keyboard-title`">{{ title }}</h2>
        <button class="icon-button" type="button" title="Cancel" aria-label="Cancel input" @click="emit('cancel')">
          <X :size="20" aria-hidden="true" />
        </button>
      </header>

      <div class="osk-preview" aria-live="polite">
        <span v-if="draft">{{ draft }}</span>
        <span v-else class="osk-placeholder">Enter a value</span>
        <span class="osk-cursor" aria-hidden="true"></span>
      </div>

      <div class="osk-body" :class="{ 'osk-body-numeric': !isTextLayout }">
        <div class="osk-key-rows">
          <div v-for="(row, rowIndex) in rows" :key="rowIndex" class="osk-key-row">
            <button
              v-for="key in row"
              :key="key"
              class="osk-key"
              type="button"
              @click="appendKey(key)"
            >
              {{ shifted && /^[a-z]$/.test(key) ? key.toUpperCase() : key }}
            </button>
          </div>

          <div v-if="isTextLayout" class="osk-key-row osk-symbol-row">
            <button class="osk-key osk-key-action" type="button" :aria-pressed="shifted" @click="shifted = !shifted">
              <ArrowBigUp :size="20" aria-hidden="true" />
              Shift
            </button>
            <button v-for="key in ['/', '-', '_', '.', ':']" :key="key" class="osk-key" type="button" @click="appendKey(key)">
              {{ key }}
            </button>
          </div>
        </div>

        <div class="osk-edit-actions">
          <button class="osk-command" type="button" @click="backspace">
            <Delete :size="20" aria-hidden="true" />
            Backspace
          </button>
          <button class="osk-command osk-command-danger" type="button" @click="clear">
            <Trash2 :size="20" aria-hidden="true" />
            Clear
          </button>
        </div>
      </div>

      <footer class="osk-footer">
        <button class="btn btn-outline-secondary" type="button" @click="emit('cancel')">Cancel</button>
        <button class="btn btn-primary" type="button" @click="finish">
          <Check :size="18" aria-hidden="true" />
          Done
        </button>
      </footer>
    </section>
  </div>
</template>