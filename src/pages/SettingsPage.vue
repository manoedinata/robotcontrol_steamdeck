<script setup>
import { ref, watch } from 'vue'
import { Camera, Save } from '@lucide/vue'

const props = defineProps({
  cameraUrl: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['saved'])

const cameraUrlDraft = ref(props.cameraUrl)
const settingsState = ref('idle')
const settingsMessage = ref('')

watch(() => props.cameraUrl, (cameraUrl) => {
  cameraUrlDraft.value = cameraUrl
})

async function saveCameraSettings() {
  settingsState.value = 'saving'
  settingsMessage.value = ''

  try {
    const settings = await window.electronAPI?.saveSettings({ cameraUrl: cameraUrlDraft.value })
    const cameraUrl = settings?.cameraUrl ?? cameraUrlDraft.value.trim()
    cameraUrlDraft.value = cameraUrl
    emit('saved', cameraUrl)
    settingsState.value = 'saved'
    settingsMessage.value = 'Camera settings saved.'
  } catch (error) {
    settingsState.value = 'error'
    settingsMessage.value = 'Settings could not be saved.'
    console.error(error)
  }
}
</script>

<template>
  <section class="settings-page" aria-labelledby="settings-heading">
    <header class="settings-header">
      <h1 id="settings-heading">Settings</h1>
      <p>Configure persistent application connections.</p>
    </header>

    <form class="settings-panel" @submit.prevent="saveCameraSettings">
      <div class="settings-panel-heading">
        <Camera :size="20" aria-hidden="true" />
        <div>
          <h2>Camera feed</h2>
          <p>HTTP image or MJPEG stream used on the Home page.</p>
        </div>
      </div>

      <div class="settings-field">
        <label for="camera-url">Camera stream URL</label>
        <input
          id="camera-url"
          v-model="cameraUrlDraft"
          class="form-control"
          type="url"
          inputmode="url"
          placeholder="http://192.168.1.20:8080/video"
          autocomplete="off"
        />
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
