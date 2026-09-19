<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useBackendConnection } from '../composables/useBackendConnection'

// One camera source that stays connected for its whole lifetime. The Home
// view mounts one instance per configured source and only shows the active
// one, so switching sources never pays a reconnect.
//
// Every source arrives the same way: the backend owns the camera transport --
// RTSP dialed directly, direct camera WebSocket pulled in and re-served -- and
// hands it to the renderer as WebRTC. The renderer only needs the stream id.
const props = defineProps({
  streamId: { type: String, default: '' },
})

const emit = defineEmits(['statusChange'])

const { cameraSignalingUrl, cameraRestarts, cameraConfigSeq } = useBackendConnection()

const videoElement = ref(null)
const cameraState = ref('idle')
const cameraError = ref(null)
// Why a feed is still connecting, when it is connecting again rather than for
// the first time. A camera that answers and then sends nothing would otherwise
// be an unexplained spinner, and the usual cause -- an RTSP transport the
// network drops -- is a setting the operator can act on.
const cameraNotice = ref(null)
const RECONNECT_DELAY_MS = 2000
// A camera that is unplugged, loses its link, or is dropped by a router keeps
// its connection open and simply stops sending. Nothing raises an error over
// that -- the peer stays connected and the last frame stays on screen, which
// reads exactly like a working camera pointed at something still -- so the
// arriving frames are counted instead: five seconds without one is a dead feed.
const FRAME_STALL_MS = 5000
const FRAME_CHECK_MS = 1000
let connectionRequest = 0
let reconnectTimer = null
let frameWatchTimer = null
let peerConnection = null
// The camera config this attempt was started against; see the restart watcher.
let attemptConfigSeq = -1

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function stopFrameWatch() {
  if (frameWatchTimer !== null) {
    clearInterval(frameWatchTimer)
    frameWatchTimer = null
  }
}

// Whole video frames this peer has received, or null while it reports no video
// at all. Counted at the receiver rather than at the decoder or the <video>
// element, because every source but one is off screen at any moment and a feed
// that is merely hidden is still a feed that is working.
async function receivedFrames(peer) {
  try {
    const stats = await peer.getStats()
    let frames = null
    stats.forEach((report) => {
      // `mediaType` is the older spelling of `kind`; missing the report
      // entirely would read as a dead feed and reconnect a working one.
      if (report.type !== 'inbound-rtp') return
      if (report.kind !== 'video' && report.mediaType !== 'video') return
      frames = report.framesReceived ?? report.framesDecoded ?? null
    })
    return frames
  } catch {
    return null
  }
}

function watchFrames(peer, requestId) {
  stopFrameWatch()
  let seen = null
  let movedAt = Date.now()

  frameWatchTimer = setInterval(async () => {
    if (peerConnection !== peer || requestId !== connectionRequest) {
      stopFrameWatch()
      return
    }
    const frames = await receivedFrames(peer)
    if (peerConnection !== peer || requestId !== connectionRequest) return

    if (frames !== null && frames !== seen) {
      seen = frames
      movedAt = Date.now()
      // Video is arriving, which is the only thing that makes this feed
      // connected. A hidden feed has no <video> event to say so.
      markConnected()
      return
    }
    if (Date.now() - movedAt < FRAME_STALL_MS) return

    stopFrameWatch()
    console.warn('[camera] No video for %ss; reconnecting', FRAME_STALL_MS / 1000, {
      streamId: props.streamId,
    })
    clearReconnectTimer()
    cameraNotice.value = seen === null
      ? 'The camera connected but sent no video. Check the RTSP transport setting.'
      : 'The camera stopped sending video.'
    // Straight into a fresh connection rather than through the error state:
    // the camera did not refuse anything, it went quiet, and the operator
    // needs to see that the feed is coming back rather than the last frame it
    // sent, which reads exactly like a working camera.
    void connectCamera(false, true)
  }, FRAME_CHECK_MS)
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
  return props.streamId.trim()
}

function scheduleReconnect() {
  clearReconnectTimer()
  if (!currentTarget()) return

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    // Ask for a fresh dial: whatever the backend holds for this source did not
    // produce a usable feed, so joining it again would fail the same way.
    void connectCamera(true, true)
  }, RECONNECT_DELAY_MS)
}

async function closePeer() {
  stopFrameWatch()
  const peer = peerConnection
  peerConnection = null
  if (!peer) return

  peer.ontrack = null
  peer.onconnectionstatechange = null
  peer.close()
  if (videoElement.value) videoElement.value.srcObject = null
}

async function connectCamera(preserveErrorState = false, restart = false) {
  connectionRequest += 1
  const requestId = connectionRequest
  const target = currentTarget()
  attemptConfigSeq = cameraConfigSeq.value

  if (!target) {
    clearReconnectTimer()
    await closePeer()
    cameraState.value = 'idle'
    cameraError.value = null
    return
  }

  if (!preserveErrorState) {
    cameraState.value = 'loading'
    cameraError.value = null
  }
  await closePeer()
  await nextTick()
  console.info('[camera] Loading camera stream', {
    streamId: props.streamId,
    reconnecting: preserveErrorState,
    restart,
  })

  const peer = new RTCPeerConnection()
  peerConnection = peer
  peer.addTransceiver('video', { direction: 'recvonly' })
  peer.ontrack = (event) => {
    if (peerConnection !== peer || requestId !== connectionRequest) return
    videoElement.value.srcObject = event.streams[0] || new MediaStream([event.track])
    // Not connected yet: a track is a promise of video, not video. A camera
    // that answered and then sent nothing would otherwise show as connected --
    // as a black rectangle, or as the last frame of the source before it.
    cameraError.value = null
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

    const response = await fetch(cameraSignalingUrl(props.streamId, restart), {
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
    // Only now: the answer waits on the backend dialing the camera, and that
    // wait is not the feed standing still.
    watchFrames(peer, requestId)
  } catch (error) {
    if (peerConnection !== peer || requestId !== connectionRequest) return
    await closePeer()
    handleCameraError(error.message || 'WebRTC camera connection failed.')
  }
}

// The feed is connected once pictures are actually arriving, whether the
// <video> element says so or the receiver's frame count does.
function markConnected() {
  if (cameraState.value === 'connected' || !currentTarget()) return
  cameraState.value = 'connected'
  cameraError.value = null
  cameraNotice.value = null
  clearReconnectTimer()
}

function handleCameraError(detail = 'WebRTC camera stream could not be loaded.') {
  cameraState.value = 'error'
  cameraError.value = detail
  console.error('[camera] Stream failed', { streamId: props.streamId })
  scheduleReconnect()
}

watch(() => props.streamId, () => {
  clearReconnectTimer()
  cameraNotice.value = null
  connectCamera()
}, { immediate: true })

// The backend re-dialed this source, because its address, its credentials, the
// camera backend or the RTSP transport changed. Whatever this feed is holding
// is a connection that no longer exists, so it is replaced at once instead of
// waiting for the peer to notice -- which is how an edited camera used to sit
// on the previous one's last frame.
watch(() => cameraRestarts.value[props.streamId] ?? 0, (restarts, previous) => {
  if (restarts === previous || !currentTarget()) return
  // Unless this attempt already began after that change was sent, in which
  // case it is negotiating against the new settings already. A feed that has
  // failed takes the news either way: it has nothing to lose, and this is
  // usually the config it was waiting for.
  if (cameraState.value !== 'error' && attemptConfigSeq >= cameraConfigSeq.value) return
  console.info('[camera] Backend re-dialed the source; reconnecting', {
    streamId: props.streamId,
  })
  clearReconnectTimer()
  void connectCamera()
})
watch(cameraState, (state) => emit('statusChange', state), { immediate: true })

onUnmounted(() => {
  clearReconnectTimer()
  stopFrameWatch()
  connectionRequest += 1
  void closePeer()
})
</script>

<template>
  <section class="camera-section">
    <div class="camera-viewport">
      <video v-show="cameraState === 'connected'" ref="videoElement" autoplay muted
        playsinline aria-label="Live camera feed" @playing="markConnected" />
      <div v-if="cameraState !== 'connected'" class="camera-message">
        <!-- Error: Unplug icon, else Camera icon -->
        <Camera v-if="cameraState !== 'error'" :size="34" aria-hidden="true" />
        <Unplug v-else :size="34" aria-hidden="true" />

        <strong v-if="cameraState === 'loading'">Connecting to camera...</strong>
        <strong v-else-if="cameraState === 'error'">Camera feed error</strong>
        <strong v-else>Camera not connected</strong>

        <span v-if="cameraState === 'error'">{{ cameraError }}</span>
        <span v-else-if="cameraState === 'loading' && cameraNotice">{{ cameraNotice }}</span>
        <span v-else-if="cameraState === 'idle'">Set the camera URL in Settings to start the feed.</span>
      </div>
    </div>
  </section>
</template>
