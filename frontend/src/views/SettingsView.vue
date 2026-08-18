<script setup>
import { nextTick, ref, watch } from 'vue'
import { Camera, Gauge, Keyboard, Network, Save } from '@lucide/vue'
import OnScreenKeyboard from '../components/OnScreenKeyboard.vue'
import { useSettingsGamepadNavigation } from '../composables/useSettingsGamepadNavigation'
import { useSettings } from '../composables/useSettings'

const emit = defineEmits(['close'])

const {
  cameraUrl,
  cameraUsername,
  cameraPassword,
  maxYVelocity,
  maxThetaVelocity,
  udpHost,
  udpPort,
  udpListenPort,
  useOnScreenKeyboard,
  saveSettings,
} = useSettings()

const streamType = ref('rtsp')
const sourceIp = ref('')
const port = ref('')
const subpath = ref('')
const username = ref(cameraUsername.value)
const password = ref(cameraPassword.value)
const maxY = ref(maxYVelocity.value)
const maxTheta = ref(maxThetaVelocity.value)
const targetHost = ref(udpHost.value)
const targetPort = ref(udpPort.value || '')
const listenPort = ref(udpListenPort.value)
const oskEnabled = ref(useOnScreenKeyboard.value)
const activeKeyboard = ref(null)
const settingsForm = ref(null)
const settingsState = ref('idle')
const settingsMessage = ref('')
let keyboardReturnControl = null
let saveQueue = Promise.resolve(true)
const { focusSaveControl } = useSettingsGamepadNavigation(activeKeyboard, () => emit('close'))

const keyboardFields = {
  sourceIp: { label: 'Source IP', layout: 'ip', maxLength: 253 },
  port: { label: 'Port', layout: 'integer', maxLength: 5 },
  subpath: { label: 'Stream subpath', layout: 'text', maxLength: 256 },
  username: { label: 'RTSP username', layout: 'credential', maxLength: 128 },
  password: { label: 'RTSP password', layout: 'credential', maxLength: 256, sensitive: true },
  maxY: { label: 'Max Y-velocity', layout: 'decimal', maxLength: 5 },
  maxTheta: { label: 'Max Theta-velocity', layout: 'decimal', maxLength: 5 },
  targetHost: { label: 'UDP target host', layout: 'hostname', maxLength: 253 },
  targetPort: { label: 'UDP target port', layout: 'integer', maxLength: 5 },
  listenPort: { label: 'UDP telemetry listen port', layout: 'integer', maxLength: 5 },
}

const fieldValues = { sourceIp, port, subpath, username, password, maxY, maxTheta, targetHost, targetPort, listenPort }

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

async function commitKeyboardValue(value) {
  const fieldName = activeKeyboard.value.name
  fieldValues[fieldName].value = value
  await closeKeyboard()
  await nextTick()
  await saveCameraSettings()
}

function handleInputKeydown(event, fieldName) {
  if (oskEnabled.value) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openKeyboard(fieldName)
    }
    return
  }

  if (event.key === 'Enter') {
    event.preventDefault()
    saveCameraSettings()
  }
}

function populateCameraFields(url) {
  if (!url) {
    streamType.value = 'rtsp'
    sourceIp.value = ''
    port.value = ''
    subpath.value = ''
    username.value = ''
    password.value = ''
    return
  }

  try {
    const parsedUrl = new URL(url)
    streamType.value = 'rtsp'
    sourceIp.value = parsedUrl.hostname
    port.value = parsedUrl.port
    subpath.value = parsedUrl.pathname
    subpath.value = subpath.value.startsWith('/') ? subpath.value : "/" + subpath.value
    username.value = decodeUrlComponent(parsedUrl.username)
    password.value = decodeUrlComponent(parsedUrl.password)
  } catch (error) {
    console.error('Could not parse the saved camera URL:', error)
  }
}

function decodeUrlComponent(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

watch(cameraUrl, (nextUrl) => {
  populateCameraFields(nextUrl)
}, { immediate: true })

watch(cameraUsername, (next) => {
  username.value = next
}, { immediate: true })

watch(cameraPassword, (next) => {
  password.value = next
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

watch(udpListenPort, (next) => {
  listenPort.value = next
}, { immediate: true })

watch(useOnScreenKeyboard, (next) => {
  oskEnabled.value = next
}, { immediate: true })

watch(oskEnabled, (enabled) => {
  if (!enabled && activeKeyboard.value) closeKeyboard()
})

async function persistSettings({ focusSave = false } = {}) {
  if (!settingsForm.value?.checkValidity()) {
    settingsForm.value?.reportValidity()
    settingsState.value = 'error'
    settingsMessage.value = 'Complete the required fields before saving.'
    return false
  }

  settingsState.value = 'saving'
  settingsMessage.value = ''

  try {
    const path = subpath.value.trim().replace(/^\/+/, '')
    const cameraUrl = `${streamType.value}://${sourceIp.value.trim()}:${port.value}${path ? `/${path}` : ''}`

    await saveSettings({
      cameraUrl,
      cameraUsername: streamType.value === 'rtsp' ? username.value : '',
      cameraPassword: streamType.value === 'rtsp' ? password.value : '',
      maxYVelocity: Number.parseFloat(maxY.value),
      maxThetaVelocity: Number.parseFloat(maxTheta.value),
      udpHost: targetHost.value.trim(),
      udpPort: targetPort.value === '' ? 0 : Number.parseInt(targetPort.value, 10),
      udpListenPort: Number.parseInt(listenPort.value, 10),
      useOnScreenKeyboard: oskEnabled.value,
    })
    settingsState.value = 'saved'
    settingsMessage.value = 'Settings saved.'
    return true
  } catch (error) {
    settingsState.value = 'error'
    settingsMessage.value = 'Settings could not be saved.'
    console.error(error)
    return false
  } finally {
    if (focusSave) await focusSaveControl()
  }
}

function saveCameraSettings(options = {}) {
  const saveOperation = saveQueue.then(() => persistSettings(options))
  saveQueue = saveOperation.catch(() => false)
  return saveOperation
}

async function saveBeforeClose() {
  if (activeKeyboard.value) await closeKeyboard()
  return saveCameraSettings()
}

defineExpose({ saveBeforeClose })
</script>

<template>
  <section id="settings-page" aria-label="Settings controls">
    <form ref="settingsForm" class="settings-panel" @submit.prevent="saveCameraSettings({ focusSave: true })">
      <div class="settings-panel-heading">
        <Camera :size="20" aria-hidden="true" />
        <div>
          <h2>Camera feed</h2>
          <p>Configure the RTSP camera source.</p>
        </div>
      </div>

      <div class="camera-settings-row">
        <fieldset class="settings-field settings-field-type">
          <legend>Stream type</legend>
          <div class="stream-type-options">
            <label class="stream-type-option" for="stream-type-rtsp">
              <input id="stream-type-rtsp" v-model="streamType" type="radio" value="rtsp" name="stream-type"
                data-gamepad-control checked />
              <span>RTSP</span>
            </label>
          </div>
        </fieldset>

        <div class="settings-field settings-field-source">
          <label for="source-ip">Source IP</label>
          <input id="source-ip" v-model.trim="sourceIp" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" placeholder="192.168.1.20"
            autocomplete="off" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('sourceIp')" @keydown="handleInputKeydown($event, 'sourceIp')" />
        </div>

        <div class="settings-field settings-field-port">
          <label for="source-port">Port</label>
          <input id="source-port" v-model="port" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="8080"
            required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('port')" @keydown="handleInputKeydown($event, 'port')" />
        </div>

        <div class="settings-field settings-field-subpath">
          <label for="stream-subpath">Subpath <span>(optional)</span></label>
          <input id="stream-subpath" v-model.trim="subpath" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" placeholder="video" autocomplete="off"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()" @click="openKeyboard('subpath')"
            @keydown="handleInputKeydown($event, 'subpath')" />
        </div>

        <div v-if="streamType === 'rtsp'" class="settings-field settings-field-credential">
          <label for="camera-username">Username <span>(optional)</span></label>
          <input id="camera-username" v-model="username" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" autocomplete="username"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()" @click="openKeyboard('username')"
            @keydown="handleInputKeydown($event, 'username')" />
        </div>

        <div v-if="streamType === 'rtsp'" class="settings-field settings-field-credential">
          <label for="camera-password">Password <span>(optional)</span></label>
          <input id="camera-password" v-model="password" class="form-control" type="password"
            :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" autocomplete="current-password"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()" @click="openKeyboard('password')"
            @keydown="handleInputKeydown($event, 'password')" />
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
            @click="openKeyboard('targetHost')" @keydown="handleInputKeydown($event, 'targetHost')" />
        </div>

        <div class="settings-field">
          <label for="udp-target-port">Target port</label>
          <input id="udp-target-port" v-model="targetPort" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="5000"
            data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('targetPort')" @keydown="handleInputKeydown($event, 'targetPort')" />
        </div>

        <div class="settings-field">
          <label for="udp-listen-port">Telemetry listen port</label>
          <input id="udp-listen-port" v-model="listenPort" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="8889"
            required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('listenPort')" @keydown="handleInputKeydown($event, 'listenPort')" />
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
            @click="openKeyboard('maxY')" @keydown="handleInputKeydown($event, 'maxY')" />
        </div>

        <div class="settings-field">
          <label for="max-theta-velocity">Max Theta-velocity <span>(angular)</span></label>
          <input id="max-theta-velocity" v-model="maxTheta" class="form-control" type="number"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" min="0.1" max="100" step="0.1"
            placeholder="10" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('maxTheta')" @keydown="handleInputKeydown($event, 'maxTheta')" />
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
      :max-length="activeKeyboard.maxLength" :title="activeKeyboard.label" :sensitive="activeKeyboard.sensitive"
      :value="String(fieldValues[activeKeyboard.name].value ?? '')" @cancel="cancelKeyboard"
      @done="commitKeyboardValue" />
  </section>
</template>
