<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'

const emit = defineEmits(['statusChange'])

// Renders the backend MJPEG endpoint. The configured source URL is sent to the
// backend through settings and is only used here to decide idle/retry state.
const { cameraUrl } = useSettings()
const { streamUrl } = useBackendConnection()

const cameraSource = ref('')
const cameraState = ref('idle')
const cameraError = ref(null)
const RECONNECT_DELAY_MS = 2000
let connectionRequest = 0
let reconnectTimer = null

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function scheduleReconnect() {
  clearReconnectTimer()
  if (!cameraUrl.value.trim()) return

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    void connectCamera(cameraUrl.value, true)
  }, RECONNECT_DELAY_MS)
}

function connectCamera(nextUrl, preserveErrorState = false) {
  connectionRequest += 1
  const nextSource = nextUrl.trim()

  if (!nextSource) {
    clearReconnectTimer()
    cameraSource.value = ''
    cameraState.value = 'idle'
    cameraError.value = null
    return
  }

  if (!preserveErrorState) {
    cameraState.value = 'loading'
    cameraError.value = null
  }
  cameraSource.value = ''
  console.info('[camera] Loading camera stream', {
    protocol: nextSource.split(':', 1)[0],
    reconnecting: preserveErrorState,
  })

  const separator = streamUrl.includes('?') ? '&' : '?'
  cameraSource.value = `${streamUrl}${separator}attempt=${connectionRequest}`
}

function handleCameraReady(event) {
  clearReconnectTimer()
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
  scheduleReconnect()
}

watch(cameraUrl, (nextUrl) => {
  clearReconnectTimer()
  connectCamera(nextUrl)
}, { immediate: true })
watch(cameraState, (state) => emit('statusChange', state), { immediate: true })

onUnmounted(() => {
  clearReconnectTimer()
  connectionRequest += 1
})
</script>

<template>
  <section class="camera-section">
    <div class="camera-viewport">
      <img v-if="cameraSource" :src="cameraSource" alt="Live IP camera feed" @load="handleCameraReady"
        @error="handleCameraError" />
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
    </div>
  </section>
</template>
