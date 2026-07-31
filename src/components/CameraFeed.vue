<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useSettings } from '../composables/useSettings'

// Renders the IP camera feed via <img>, which covers HTTP/HTTPS snapshots and
// MJPEG streams without an extra player. The camera URL comes from shared
// settings and reconnects reactively. Diagnostic logs use the [camera] prefix.
const { cameraUrl } = useSettings()

const cameraSource = ref('')
const cameraState = ref('idle')
const cameraError = ref(null)
let connectionRequest = 0

async function connectCamera(nextUrl) {
  const request = ++connectionRequest
  const nextSource = nextUrl.trim()
  cameraError.value = null

  if (!nextSource) {
    cameraSource.value = ''
    cameraState.value = 'idle'
    window.electronAPI?.resolveCameraStream?.('')
    return
  }

  cameraState.value = 'loading'
  cameraSource.value = ''
  console.info('[camera] Loading camera stream', { protocol: nextSource.split(':', 1)[0] })

  try {
    const resolvedSource = window.electronAPI?.resolveCameraStream
      ? await window.electronAPI.resolveCameraStream(nextSource)
      : nextSource

    if (request !== connectionRequest) return
    cameraSource.value = resolvedSource
  } catch (error) {
    if (request !== connectionRequest) return
    cameraState.value = 'error'
    cameraError.value = error?.message || 'Camera stream could not be started.'
    console.error('[camera] Stream setup failed:', error)
  }
}

function handleCameraReady(event) {
  cameraState.value = 'connected'
  cameraError.value = null
  console.info('[camera] Stream ready', {
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
    protocol: cameraUrl.value.split(':', 1)[0] || 'unknown',
    complete: image.complete,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  })
}

watch(cameraUrl, connectCamera, { immediate: true })

onUnmounted(() => {
  connectionRequest += 1
  window.electronAPI?.resolveCameraStream?.('')
})
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
