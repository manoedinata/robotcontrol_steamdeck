<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Camera, Gauge, Keyboard, Network, Plus, Save, Trash2 } from '@lucide/vue'
import OnScreenKeyboard from '../components/OnScreenKeyboard.vue'
import { useSettingsGamepadNavigation } from '../composables/useSettingsGamepadNavigation'
import { useSettings } from '../composables/useSettings'

const emit = defineEmits(['close'])

const {
  cameraSources,
  activeCameraIndex,
  cameraBackend,
  ptzIp,
  maxYVelocity,
  maxThetaVelocity,
  udpHost,
  udpPort,
  udpListenPort,
  useOnScreenKeyboard,
  selectCamera,
  saveSettings,
} = useSettings()

// One editable form row per configured camera source.
const cameras = ref([])
const backend = ref(cameraBackend.value)
const ptzAddress = ref(ptzIp.value)
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

const cameraKeyboardFields = {
  sourceIp: { label: 'Source IP', layout: 'ip', maxLength: 253 },
  port: { label: 'Port', layout: 'integer', maxLength: 5 },
  subpath: { label: 'Stream subpath', layout: 'text', maxLength: 256 },
  username: { label: 'RTSP username', layout: 'credential', maxLength: 128 },
  password: { label: 'RTSP password', layout: 'credential', maxLength: 256, sensitive: true },
}

const keyboardFields = {
  maxY: { label: 'Max Y-velocity', layout: 'decimal', maxLength: 5 },
  maxTheta: { label: 'Max Theta-velocity', layout: 'decimal', maxLength: 5 },
  targetHost: { label: 'UDP target host', layout: 'hostname', maxLength: 253 },
  targetPort: { label: 'UDP target port', layout: 'integer', maxLength: 5 },
  listenPort: { label: 'UDP telemetry listen port', layout: 'integer', maxLength: 5 },
  ptzAddress: { label: 'PTZ camera IP', layout: 'ip', maxLength: 253 },
}

const fieldValues = { maxY, maxTheta, targetHost, targetPort, listenPort, ptzAddress }

// Camera fields are addressed as `camera:<index>:<field>` so the on-screen
// keyboard can target any source in the list.
function cameraField(index, field) {
  return `camera:${index}:${field}`
}

function parseFieldName(name) {
  const [prefix, index, field] = name.split(':')
  if (prefix !== 'camera') return null
  return { index: Number(index), field }
}

function readField(name) {
  const target = parseFieldName(name)
  return target ? cameras.value[target.index]?.[target.field] : fieldValues[name].value
}

function writeField(name, value) {
  const target = parseFieldName(name)
  if (target) cameras.value[target.index][target.field] = value
  else fieldValues[name].value = value
}

function describeField(name) {
  const target = parseFieldName(name)
  if (!target) return keyboardFields[name]
  const descriptor = cameraKeyboardFields[target.field]
  return { ...descriptor, label: `Camera ${target.index + 1} ${descriptor.label}` }
}

const activeKeyboardValue = computed(() => activeKeyboard.value
  ? String(readField(activeKeyboard.value.name) ?? '')
  : '')

function openKeyboard(fieldName) {
  if (!oskEnabled.value) return
  keyboardReturnControl = document.activeElement
  activeKeyboard.value = { name: fieldName, ...describeField(fieldName) }
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
  writeField(activeKeyboard.value.name, value)
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

function createCameraForm(source = {}) {
  const form = {
    streamType: source.type ?? 'rtsp',
    sourceIp: '',
    port: '',
    subpath: '',
    username: source.username ?? '',
    password: source.password ?? '',
  }

  if (!source.url) return form

  try {
    const parsedUrl = new URL(source.url)
    form.streamType = parsedUrl.protocol.toLowerCase() === 'ws:' ? 'websocket' : 'rtsp'
    form.sourceIp = parsedUrl.hostname
    form.port = parsedUrl.port
    form.subpath = parsedUrl.pathname.startsWith('/') ? parsedUrl.pathname : `/${parsedUrl.pathname}`
  } catch (error) {
    console.error('Could not parse the saved camera URL:', error)
  }

  return form
}

function addCamera() {
  cameras.value.push(createCameraForm())
}

async function removeCamera(index) {
  // Keep at least one row so the form always has an editable source.
  if (cameras.value.length <= 1) return
  cameras.value.splice(index, 1)
  if (activeCameraIndex.value >= cameras.value.length) selectCamera(cameras.value.length - 1)
  await saveCameraSettings()
}

watch(cameraSources, (sources) => {
  cameras.value = sources.map(createCameraForm)
}, { immediate: true })

watch(cameraBackend, (next) => {
  backend.value = next
}, { immediate: true })

watch(ptzIp, (next) => {
  ptzAddress.value = next
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
    const cameraSourcePayload = cameras.value.map((camera) => {
      const path = camera.subpath.trim().replace(/^\/+/, '')
      const scheme = camera.streamType === 'websocket' ? 'ws' : 'rtsp'

      return {
        url: `${scheme}://${camera.sourceIp.trim()}:${camera.port}${camera.streamType === 'rtsp' && path ? `/${path}` : ''}`,
        type: camera.streamType,
        username: camera.streamType === 'rtsp' ? camera.username : '',
        password: camera.streamType === 'rtsp' ? camera.password : '',
      }
    })

    await saveSettings({
      cameraSources: cameraSourcePayload,
      activeCameraIndex: Math.min(activeCameraIndex.value, cameraSourcePayload.length - 1),
      cameraBackend: backend.value,
      ptzIp: ptzAddress.value.trim(),
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
          <h2>Camera feeds</h2>
          <p>Configure one or more camera sources. Press Circle on the gamepad to switch the live feed.</p>
        </div>
      </div>

      <div v-for="(camera, index) in cameras" :key="index" class="camera-source-group"
        :class="{ active: index === activeCameraIndex }">
        <div class="camera-source-header">
          <strong>Camera {{ index + 1 }}<span v-if="index === activeCameraIndex"> (live)</span></strong>
          <div class="camera-source-actions">
            <button v-if="index !== activeCameraIndex" class="btn btn-secondary btn-sm" type="button"
              :aria-label="`Show camera ${index + 1}`" data-gamepad-control @click="selectCamera(index)">
              Show
            </button>
            <button v-if="cameras.length > 1" class="btn btn-secondary btn-sm" type="button"
              :aria-label="`Remove camera ${index + 1}`" data-gamepad-control @click="removeCamera(index)">
              <Trash2 :size="15" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div class="camera-settings-row">
          <fieldset class="settings-field settings-field-type">
            <legend>Stream type</legend>
            <div class="stream-type-options">
              <label class="stream-type-option" :for="`stream-type-rtsp-${index}`">
                <input :id="`stream-type-rtsp-${index}`" v-model="camera.streamType" type="radio" value="rtsp"
                  :name="`stream-type-${index}`" data-gamepad-control />
                <span>RTSP</span>
              </label>
              <label class="stream-type-option" :for="`stream-type-websocket-${index}`">
                <input :id="`stream-type-websocket-${index}`" v-model="camera.streamType" type="radio" value="websocket"
                  :name="`stream-type-${index}`" data-gamepad-control />
                <span>WebSocket</span>
              </label>
            </div>
          </fieldset>

          <div class="settings-field settings-field-source">
            <label :for="`source-ip-${index}`">Source IP</label>
            <input :id="`source-ip-${index}`" v-model.trim="camera.sourceIp" class="form-control" type="text"
              :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" placeholder="192.168.1.20"
              autocomplete="off" required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
              @click="openKeyboard(cameraField(index, 'sourceIp'))"
              @keydown="handleInputKeydown($event, cameraField(index, 'sourceIp'))" />
          </div>

          <div class="settings-field settings-field-port">
            <label :for="`source-port-${index}`">Port</label>
            <input :id="`source-port-${index}`" v-model="camera.port" class="form-control" type="number"
              :inputmode="oskEnabled ? 'none' : 'numeric'" :readonly="oskEnabled" min="1" max="65535" placeholder="8080"
              required data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
              @click="openKeyboard(cameraField(index, 'port'))"
              @keydown="handleInputKeydown($event, cameraField(index, 'port'))" />
          </div>

          <div v-if="camera.streamType === 'rtsp'" class="settings-field settings-field-subpath">
            <label :for="`stream-subpath-${index}`">Subpath <span>(optional)</span></label>
            <input :id="`stream-subpath-${index}`" v-model.trim="camera.subpath" class="form-control" type="text"
              :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" placeholder="video" autocomplete="off"
              data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
              @click="openKeyboard(cameraField(index, 'subpath'))"
              @keydown="handleInputKeydown($event, cameraField(index, 'subpath'))" />
          </div>

          <div v-if="camera.streamType === 'rtsp'" class="settings-field settings-field-credential">
            <label :for="`camera-username-${index}`">Username <span>(optional)</span></label>
            <input :id="`camera-username-${index}`" v-model="camera.username" class="form-control" type="text"
              :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" autocomplete="username"
              data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
              @click="openKeyboard(cameraField(index, 'username'))"
              @keydown="handleInputKeydown($event, cameraField(index, 'username'))" />
          </div>

          <div v-if="camera.streamType === 'rtsp'" class="settings-field settings-field-credential">
            <label :for="`camera-password-${index}`">Password <span>(optional)</span></label>
            <input :id="`camera-password-${index}`" v-model="camera.password" class="form-control" type="password"
              :inputmode="oskEnabled ? 'none' : 'text'" :readonly="oskEnabled" autocomplete="current-password"
              data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
              @click="openKeyboard(cameraField(index, 'password'))"
              @keydown="handleInputKeydown($event, cameraField(index, 'password'))" />
          </div>
        </div>
      </div>

      <div class="camera-add-row">
        <button class="btn btn-secondary" type="button" data-gamepad-control @click="addCamera">
          <Plus :size="16" aria-hidden="true" />
          Add camera source
        </button>
      </div>

      <div v-if="cameras.some((camera) => camera.streamType === 'rtsp')" class="camera-backend-row">
        <div class="settings-field">
          <label for="camera-backend">WebRTC backend</label>
          <select id="camera-backend" v-model="backend" class="form-select" data-gamepad-control>
            <option value="go2rtc">go2rtc</option>
            <option value="aiortc">aiortc</option>
          </select>
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
        <Camera :size="20" aria-hidden="true" />
        <div>
          <h2>Camera rotation (PTZ)</h2>
          <p>Address of the camera that responds to rotation commands. LB/RB rotate left/right; LT/RT tilt down/up.</p>
        </div>
      </div>

      <div class="udp-settings-row">
        <div class="settings-field">
          <label for="ptz-ip">PTZ camera IP <span>(optional)</span></label>
          <input id="ptz-ip" v-model.trim="ptzAddress" class="form-control" type="text"
            :inputmode="oskEnabled ? 'none' : 'decimal'" :readonly="oskEnabled" placeholder="192.168.1.64"
            autocomplete="off" data-gamepad-control @pointerdown="oskEnabled && $event.preventDefault()"
            @click="openKeyboard('ptzAddress')" @keydown="handleInputKeydown($event, 'ptzAddress')" />
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
      :value="activeKeyboardValue" @cancel="cancelKeyboard" @done="commitKeyboardValue" />
  </section>
</template>
