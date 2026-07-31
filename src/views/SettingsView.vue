<script setup>
import { ref, watch } from 'vue'
import { Camera, Gauge, Save } from '@lucide/vue'
import { useSettings } from '../composables/useSettings'

const { cameraUrl, maxYVelocity, maxThetaVelocity, saveSettings } = useSettings()

const streamType = ref('http')
const sourceIp = ref('')
const port = ref('')
const subpath = ref('')
const maxY = ref(maxYVelocity.value)
const maxTheta = ref(maxThetaVelocity.value)
const settingsState = ref('idle')
const settingsMessage = ref('')

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
    })
    settingsState.value = 'saved'
    settingsMessage.value = 'Settings saved.'
  } catch (error) {
    settingsState.value = 'error'
    settingsMessage.value = 'Settings could not be saved.'
    console.error(error)
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
          <select id="stream-type" v-model="streamType" class="form-select">
            <option value="http">HTTP</option>
            <option value="rtsp">RTSP</option>
          </select>
        </div>

        <div class="settings-field settings-field-source">
          <label for="source-ip">Source IP</label>
          <input
            id="source-ip"
            v-model.trim="sourceIp"
            class="form-control"
            type="text"
            inputmode="decimal"
            placeholder="192.168.1.20"
            autocomplete="off"
            required
          />
        </div>

        <div class="settings-field settings-field-port">
          <label for="source-port">Port</label>
          <input
            id="source-port"
            v-model="port"
            class="form-control"
            type="number"
            inputmode="numeric"
            min="1"
            max="65535"
            placeholder="8080"
            required
          />
        </div>

        <div class="settings-field settings-field-subpath">
          <label for="stream-subpath">Subpath <span>(optional)</span></label>
          <input
            id="stream-subpath"
            v-model.trim="subpath"
            class="form-control"
            type="text"
            placeholder="video"
            autocomplete="off"
          />
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
          <input
            id="max-y-velocity"
            v-model="maxY"
            class="form-control"
            type="number"
            inputmode="decimal"
            min="0.1"
            max="100"
            step="0.1"
            placeholder="10"
            required
          />
        </div>

        <div class="settings-field">
          <label for="max-theta-velocity">Max Theta-velocity <span>(angular)</span></label>
          <input
            id="max-theta-velocity"
            v-model="maxTheta"
            class="form-control"
            type="number"
            inputmode="decimal"
            min="0.1"
            max="100"
            step="0.1"
            placeholder="10"
            required
          />
        </div>
      </div>

      <div class="settings-actions">
        <span
          class="settings-message"
          :class="{ error: settingsState === 'error' }"
          role="status"
        >
          {{ settingsMessage }}
        </span>
        <button class="btn btn-primary" type="submit" :disabled="settingsState === 'saving'">
          <Save :size="17" aria-hidden="true" />
          {{ settingsState === 'saving' ? 'Saving...' : 'Save settings' }}
        </button>
      </div>
    </form>
  </section>
</template>
