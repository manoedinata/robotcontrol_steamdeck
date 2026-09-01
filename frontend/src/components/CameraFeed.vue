<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useBackendConnection } from '../composables/useBackendConnection'
import { useCameraWebSocket } from '../composables/useCameraWebSocket'

// One camera source that stays connected for its whole lifetime. The Home
// view mounts one instance per configured source and only shows the active
// one, so switching sources never pays a reconnect.
const props = defineProps({
  // 'rtsp' streams are dialed by the backend and addressed by `streamId`;
  // 'websocket' streams are reached directly from the renderer via `wsUrl`.
  mode: { type: String, required: true },
  streamId: { type: String, default: '' },
  wsUrl: { type: String, default: '' },
})

const emit = defineEmits(['statusChange'])

const { cameraSignalingUrl } = useBackendConnection()

const videoElement = ref(null)
const canvasElement = ref(null)
const cameraState = ref('idle')
const cameraError = ref(null)
const RECONNECT_DELAY_MS = 2000
let connectionRequest = 0
let reconnectTimer = null
let peerConnection = null

const cameraWebSocket = useCameraWebSocket(canvasElement, (state, error) => {
  if (state === 'connected') {
    cameraState.value = state
    cameraError.value = null
    clearReconnectTimer()
  } else if (state === 'error') {
    handleCameraError(error || 'Camera WebSocket failed.')
  } else {
    cameraState.value = state
  }
})

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function waitForIceGatheringComplete(peer, timeoutMs = 5000) {
  if (peer.iceGatheringState === 'complete') return Promise.resolve()

  return new Promise((resolve) => {
    let timeoutId = null

    const finish = () => {
      peer.removeEventListener('icegatheringstatechange', handleStateChange)
      if (timeoutId !== null) clearTimeout(timeoutId)
      resolve()
    }

    const handleStateChange = () => {
      if (peer.iceGatheringState === 'complete') finish()
    }

    peer.addEventListener('icegatheringstatechange', handleStateChange)
    timeoutId = setTimeout(finish, timeoutMs)
  })
}

function currentTarget() {
  return props.mode === 'websocket' ? props.wsUrl.trim() : props.streamId.trim()
}

function scheduleReconnect() {
  clearReconnectTimer()
  if (!currentTarget()) return

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    void connectCamera(true)
  }, RECONNECT_DELAY_MS)
}

async function closePeer() {
  const peer = peerConnection
  peerConnection = null
  if (!peer) return

  peer.ontrack = null
  peer.onconnectionstatechange = null
  peer.close()
  if (videoElement.value) videoElement.value.srcObject = null
}

async function closeCameraWebSocket() {
  cameraWebSocket.close()
  if (canvasElement.value) {
    const context = canvasElement.value.getContext('2d')
    context?.clearRect(0, 0, canvasElement.value.width, canvasElement.value.height)
  }
}

async function connectCamera(preserveErrorState = false) {
  connectionRequest += 1
  const requestId = connectionRequest
  const target = currentTarget()

  if (!target) {
    clearReconnectTimer()
    await closePeer()
    await closeCameraWebSocket()
    cameraState.value = 'idle'
    cameraError.value = null
    return
  }

  if (!preserveErrorState) {
    cameraState.value = 'loading'
    cameraError.value = null
  }
  await closePeer()
  await closeCameraWebSocket()
  await nextTick()
  console.info('[camera] Loading camera stream', {
    mode: props.mode,
    streamId: props.mode === 'rtsp' ? props.streamId : undefined,
    reconnecting: preserveErrorState,
  })

  if (props.mode === 'websocket') {
    try {
      await cameraWebSocket.connect(target)
      if (requestId === connectionRequest) clearReconnectTimer()
    } catch (error) {
      if (requestId === connectionRequest) {
        handleCameraError(error.message || 'Camera WebSocket failed.')
      }
    }
    return
  }

  const peer = new RTCPeerConnection()
  peerConnection = peer
  peer.addTransceiver('video', { direction: 'recvonly' })
  peer.ontrack = (event) => {
    if (peerConnection !== peer || requestId !== connectionRequest) return
    videoElement.value.srcObject = event.streams[0] || new MediaStream([event.track])
    cameraState.value = 'connected'
    cameraError.value = null
    clearReconnectTimer()
  }
  peer.onconnectionstatechange = () => {
    if (peerConnection !== peer || requestId !== connectionRequest) return
    if (['failed', 'closed', 'disconnected'].includes(peer.connectionState)) {
      handleCameraError('WebRTC connection closed.')
    }
  }

  try {
    const offer = await peer.createOffer()
    await peer.setLocalDescription(offer)
    await waitForIceGatheringComplete(peer)

    if (peerConnection !== peer || requestId !== connectionRequest) return

    const response = await fetch(cameraSignalingUrl(props.streamId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sdp: peer.localDescription.sdp,
        type: peer.localDescription.type,
      }),
    })
    if (!response.ok) {
      const error = await response.json().catch(() => ({}))
      throw new Error(error.error || `WebRTC signaling failed (${response.status})`)
    }
    await peer.setRemoteDescription(await response.json())
  } catch (error) {
    if (peerConnection !== peer || requestId !== connectionRequest) return
    await closePeer()
    handleCameraError(error.message || 'WebRTC camera connection failed.')
  }
}

function handleCameraError(detail = 'WebRTC camera stream could not be loaded.') {
  cameraState.value = 'error'
  cameraError.value = detail
  console.error('[camera] Stream failed', { mode: props.mode })
  scheduleReconnect()
}

watch([() => props.mode, () => props.streamId, () => props.wsUrl], () => {
  clearReconnectTimer()
  connectCamera()
}, { immediate: true })
watch(cameraState, (state) => emit('statusChange', state), { immediate: true })

onUnmounted(() => {
  clearReconnectTimer()
  connectionRequest += 1
  void closePeer()
  void closeCameraWebSocket()
})
</script>

<template>
  <section class="camera-section">
    <div class="camera-viewport">
      <video v-if="mode === 'rtsp'" v-show="cameraState === 'connected'" ref="videoElement" autoplay muted
        playsinline aria-label="Live camera feed" />
      <canvas v-else v-show="cameraState === 'connected'" ref="canvasElement" aria-label="Live camera feed" />
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
