<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Camera, Gauge, Keyboard, Network, Save } from '@lucide/vue'
import OnScreenKeyboard from '../components/OnScreenKeyboard.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'

const { registerHandler } = useGamepad()
let unregisterGamepadHandler

const {
  cameraUrl,
  maxYVelocity,
  maxThetaVelocity,
  udpHost,
  udpPort,
  useOnScreenKeyboard,
  saveSettings,
} = useSettings()

const streamType = ref('http')
const sourceIp = ref('')
const port = ref('')
const subpath = ref('')
const maxY = ref(maxYVelocity.value)
const maxTheta = ref(maxThetaVelocity.value)
const targetHost = ref(udpHost.value)
const targetPort = ref(udpPort.value || '')
const oskEnabled = ref(useOnScreenKeyboard.value)
const activeKeyboard = ref(null)
const settingsState = ref('idle')
const settingsMessage = ref('')
let keyboardReturnControl = null

const keyboardFields = {
  sourceIp: { label: 'Source IP', layout: 'ip', maxLength: 253 },
  port: { label: 'Port', layout: 'integer', maxLength: 5 },
  subpath: { label: 'Stream subpath', layout: 'text', maxLength: 256 },
  maxY: { label: 'Max Y-velocity', layout: 'decimal', maxLength: 5 },
  maxTheta: { label: 'Max Theta-velocity', layout: 'decimal', maxLength: 5 },
  targetHost: { label: 'UDP target host', layout: 'ip', maxLength: 253 },
  targetPort: { label: 'UDP target port', layout: 'integer', maxLength: 5 },
}

const fieldValues = { sourceIp, port, subpath, maxY, maxTheta, targetHost, targetPort }

function openKeyboard(fieldName) {
  if (!oskEnabled.value) return
  keyboardReturnControl = document.activeElement
  activeKeyboard.value = { name: fieldName, ...keyboardFields[fieldName] }
}

async function closeKeyboard() {
  activeKeyboard.value = null
  await nextTick()
  keyboardReturnControl?.focus({ preventScroll: true })
  keyboardReturnControl = null
}

function cancelKeyboard() {
  closeKeyboard()
}

function commitKeyboardValue(value) {
  fieldValues[activeKeyboard.value.name].value = value
  closeKeyboard()
}

function gamepadControls() {
  return [...document.querySelectorAll('#settings-page [data-gamepad-control]:not(:disabled)')]
}

function focusControlInDirection(direction) {
  const controls = gamepadControls()
  const current = document.activeElement
  if (!controls.includes(current)) {
    controls[0]?.focus()
    return
  }

  const currentRect = current.getBoundingClientRect()
  const currentX = currentRect.left + currentRect.width / 2
  const currentY = currentRect.top + currentRect.height / 2
  const candidates = controls.filter((control) => {
    if (control === current) return false
    const rect = control.getBoundingClientRect()
    const x = rect.left + rect.width / 2
    const y = rect.top + rect.height / 2
    if (direction === 'up') return y < currentY - 4
    if (direction === 'down') return y > currentY + 4
    if (direction === 'left') return x < currentX - 4
    return x > currentX + 4
  })

  const next = candidates.sort((a, b) => {
    const score = (control) => {
      const rect = control.getBoundingClientRect()
      const x = rect.left + rect.width / 2
      const y = rect.top + rect.height / 2
      const primary = ['up', 'down'].includes(direction) ? Math.abs(y - currentY) : Math.abs(x - currentX)
      const secondary = ['up', 'down'].includes(direction) ? Math.abs(x - currentX) : Math.abs(y - currentY)
      return primary + secondary * 2
    }
    return score(a) - score(b)
  })[0]

  next?.focus({ preventScroll: true })
  next?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

function handleGamepadNavigation(action) {
  if (activeKeyboard.value || !document.activeElement?.closest?.('#settings-page')) return false

  if (['up', 'down', 'left', 'right'].includes(action)) {
    focusControlInDirection(action)
    return true
  }
  if (action === 'activate') {
    const control = document.activeElement
    if (control instanceof HTMLSelectElement) {
      const nextIndex = (control.selectedIndex + 1) % control.options.length
      control.selectedIndex = nextIndex
      control.dispatchEvent(new Event('change', { bubbles: true }))
    } else {
      control?.click()
    }
    return true
  }
  if (action === 'cancel') {
    document.querySelector(`.sidebar [data-route-name="settings"]`)?.focus()
    return true
  }
  return false
}

function populateCameraFields(url) {
  if (!url) {
    streamType.value = 'http'
    sourceIp.value = ''
    port.value = ''
    subpath.value = ''
    return
  }

  try {
    const parsedUrl = new URL(url)
    streamType.value = parsedUrl.protocol === 'rtsp:' ? 'rtsp' : 'http'
    sourceIp.value = parsedUrl.hostname
    port.value = parsedUrl.port
    subpath.value = parsedUrl.pathname
    subpath.value = subpath.value.startsWith('/') ? subpath.value : "/" + subpath.value
  } catch (error) {
    console.error('Could not parse the saved camera URL:', error)
  }
}

watch(cameraUrl, (nextUrl) => {
  populateCameraFields(nextUrl)
}, { immediate: true })

watch(maxYVelocity, (next) => {
  maxY.value = next
}, { immediate: true })

watch(maxThetaVelocity, (next) => {
  maxTheta.value = next
}, { immediate: true })

watch(udpHost, (next) => {
  targetHost.value = next
}, { immediate: true })

watch(udpPort, (next) => {
  targetPort.value = next || ''
}, { immediate: true })

watch(useOnScreenKeyboard, (next) => {
  oskEnabled.value = next
}, { immediate: true })

watch(oskEnabled, (enabled) => {
  if (!enabled && activeKeyboard.value) closeKeyboard()
})

onMounted(() => {
  unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 50)
})

onBeforeUnmount(() => unregisterGamepadHandler?.())

async function saveCameraSettings() {
  settingsState.value = 'saving'
  settingsMessage.value = ''

  try {
    const path = subpath.value.trim().replace(/^\/+/, '')
    const cameraUrl = `${streamType.value}://${sourceIp.value.trim()}:${port.value}${path ? `/${path}` : ''}`

    await saveSettings({
      cameraUrl,
      maxYVelocity: Number.parseFloat(maxY.value),
      maxThetaVelocity: Number.parseFloat(maxTheta.value),
      udpHost: targetHost.value.trim(),
      udpPort: targetPort.value === '' ? 0 : Number.parseInt(targetPort.value, 10),
      useOnScreenKeyboard: oskEnabled.value,
    })
    settingsState.value = 'saved'
    settingsMessage.value = 'Settings saved.'
  } catch (error) {
    settingsState.value = 'error'
    settingsMessage.value = 'Settings could not be saved.'
    console.error(error)
  } finally {
    await nextTick()
    document.querySelector('#settings-page [data-gamepad-save]')?.focus({ preventScroll: true })
  }
}
</script>

<template>
  <section id="settings-page" aria-labelledby="settings-heading">
    <header class="settings-header">
      <h1 id="settings-heading">Settings</h1>
    </header>

    <form class="settings-panel" @submit.prevent="saveCameraSettings">
      <div class="settings-panel-heading">
        <Camera :size="20" aria-hidden="true" />
        <div>
          <h2>Camera feed</h2>
          <p>Configure the camera stream source.</p>
        </div>
      </div>

      <div class="camera-settings-row">
        <div class="settings-field settings-field-type">
          <label for="stream-type">Stream type</label>
          <select id="stream-type" v-model="streamType" class="form-select" data-gamepad-control>
            <option value="http">HTTP</option>
            <option value="rtsp">RTSP</option>
          </select>
        </div>

        <div class="settings-field settings-field-source">
          <label for="source-ip">Source IP</label>
          <input id="source-ip" v-model.trim="sourceIp" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" placeholder="192.168.1.20"
            autocomplete="off" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('sourceIp')" @keydown.enter.prevent="openKeyboard('sourceIp')"
            @keydown.space.prevent="openKeyboard('sourceIp')" />
        </div>

        <div class="settings-field settings-field-port">
          <label for="source-port">Port</label>
          <input id="source-port" v-model="port" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="8080"
            required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('port')" @keydown.enter.prevent="openKeyboard('port')"
            @keydown.space.prevent="openKeyboard('port')" />
        </div>

        <div class="settings-field settings-field-subpath">
          <label for="stream-subpath">Subpath <span>(optional)</span></label>
          <input id="stream-subpath" v-model.trim="subpath" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" placeholder="video" autocomplete="off"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()" @click="openKeyboard('subpath')"
            @keydown.enter.prevent="openKeyboard('subpath')" @keydown.space.prevent="openKeyboard('subpath')" />
        </div>
      </div>

      <div class="settings-panel-heading settings-panel-heading-divided">
        <Network :size="20" aria-hidden="true" />
        <div>
          <h2>UDP destination</h2>
          <p>Set the robot endpoint for velocity commands.</p>
        </div>
      </div>

      <div class="udp-settings-row">
        <div class="settings-field">
          <label for="udp-target-host">Target host</label>
          <input id="udp-target-host" v-model.trim="targetHost" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" placeholder="192.168.1.30"
            autocomplete="off" data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('targetHost')" @keydown.enter.prevent="openKeyboard('targetHost')"
            @keydown.space.prevent="openKeyboard('targetHost')" />
        </div>

        <div class="settings-field">
          <label for="udp-target-port">Target port</label>
          <input id="udp-target-port" v-model="targetPort" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="5000"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('targetPort')" @keydown.enter.prevent="openKeyboard('targetPort')"
            @keydown.space.prevent="openKeyboard('targetPort')" />
        </div>
      </div>

      <div class="settings-panel-heading settings-panel-heading-divided">
        <Gauge :size="20" aria-hidden="true" />
        <div>
          <h2>Robot controls</h2>
          <p>Set the maximum velocity for each axis. Joystick output scales to these limits.</p>
        </div>
      </div>

      <div class="velocity-settings-row">
        <div class="settings-field">
          <label for="max-y-velocity">Max Y-velocity <span>(linear)</span></label>
          <input id="max-y-velocity" v-model="maxY" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" min="0.1" max="100" step="0.1"
            placeholder="10" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('maxY')" @keydown.enter.prevent="openKeyboard('maxY')"
            @keydown.space.prevent="openKeyboard('maxY')" />
        </div>

        <div class="settings-field">
          <label for="max-theta-velocity">Max Theta-velocity <span>(angular)</span></label>
          <input id="max-theta-velocity" v-model="maxTheta" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" min="0.1" max="100" step="0.1"
            placeholder="10" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('maxTheta')" @keydown.enter.prevent="openKeyboard('maxTheta')"
            @keydown.space.prevent="openKeyboard('maxTheta')" />
        </div>
      </div>

      <div class="settings-panel-heading settings-panel-heading-divided">
        <Keyboard :size="20" aria-hidden="true" />
        <div>
          <h2>Text input</h2>
          <p>Use the built-in keyboard for Settings fields.</p>
        </div>
      </div>

      <div class="keyboard-setting-row">
        <div>
          <strong>On-screen keyboard</strong>
          <span>Use On-screen Keyboard instead of Steam keyboard.</span>
        </div>
        <div class="form-check form-switch">
          <input id="osk-enabled" v-model="oskEnabled" class="form-check-input" type="checkbox" role="switch"
            data-gamepad-control />
          <label class="form-check-label" for="osk-enabled">{{ oskEnabled ? 'Enabled' : 'Disabled' }}</label>
        </div>
      </div>

      <div class="settings-actions">
        <span class="settings-message" :class="{ error: settingsState === 'error' }" role="status">
          {{ settingsMessage }}
        </span>
        <button class="btn btn-primary" type="submit" :disabled="settingsState === 'saving'" data-gamepad-control
          data-gamepad-save>
          <Save :size="17" aria-hidden="true" />
          {{ settingsState === 'saving' ? 'Saving...' : 'Save settings' }}
        </button>
      </div>
    </form>

    <OnScreenKeyboard v-if="activeKeyboard" :key="activeKeyboard.name" :layout="activeKeyboard.layout"
      :max-length="activeKeyboard.maxLength" :title="activeKeyboard.label"
      :value="String(fieldValues[activeKeyboard.name].value ?? '')" @cancel="cancelKeyboard"
      @done="commitKeyboardValue" />
  </section>
</template>
