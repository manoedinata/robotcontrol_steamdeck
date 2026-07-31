<script setup>
import { ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useSettings } from '../composables/useSettings'

// Renders the IP camera feed via <img>, which covers HTTP/HTTPS snapshots and
// MJPEG streams without an extra player. The camera URL comes from shared
// settings and reconnects reactively. Diagnostic logs use the [camera] prefix.
const { cameraUrl } = useSettings()

const cameraSource = ref('')
const cameraState = ref('idle')
const cameraError = ref(null)

function connectCamera(nextUrl) {
  const nextSource = nextUrl.trim()
  cameraError.value = null

  if (!nextSource) {
    cameraSource.value = ''
    cameraState.value = 'idle'
    return
  }

  cameraState.value = 'loading'
  cameraSource.value = nextSource
  console.info('[camera] Loading stream:', nextSource)
}

function handleCameraReady(event) {
  cameraState.value = 'connected'
  cameraError.value = null
  console.info('[camera] Stream ready', {
    currentSrc: event.currentTarget.currentSrc,
    width: event.currentTarget.naturalWidth,
    height: event.currentTarget.naturalHeight,
  })
}

function handleCameraError(event) {
  const image = event.currentTarget
  const detail = 'Image or MJPEG stream could not be loaded.'

  cameraState.value = 'error'
  cameraError.value = detail
  console.error('[camera] Stream failed', {
    requestedUrl: cameraUrl.value,
    currentSrc: image.currentSrc,
    complete: image.complete,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  })
}

watch(cameraUrl, connectCamera, { immediate: true })
</script>

<template>
  <section class="camera-section">
    <div class="camera-viewport">
      <img
        v-if="cameraSource"
        :src="cameraSource"
        alt="Live IP camera feed"
        @load="handleCameraReady"
        @error="handleCameraError"
      />
      <div v-if="cameraState !== 'connected'" class="camera-message">
          <!-- Error: Unplug icon, else Camera icon -->
        <Camera v-if="cameraState !== 'error'" :size="34" aria-hidden="true" />
        <Unplug v-else :size="34" aria-hidden="true" />

        <strong v-if="cameraState === 'loading'">Connecting to camera...</strong>
        <strong v-else-if="cameraState === 'error'">Camera feed error</strong>
        <strong v-else>Camera not connected</strong>

        <span v-if="cameraState === 'error'">{{ cameraError }}</span>
        <span v-else-if="cameraState === 'idle'">Set the camera URL in Settings to start the feed.</span>
      </div>
      <span :class="`live-indicator ${cameraState}`">
          <span v-if="cameraState === 'connected'">Connected</span>
          <span v-else-if="cameraState === 'loading'">Connecting...</span>
          <span v-else-if="cameraState === 'error'">Error</span>
          <span v-else>Disconnected</span>
      </span>
    </div>
  </section>
</template>
