import { readonly, ref } from 'vue'

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000'
const RECONNECT_DELAY_MS = 2000
const TELEMETRY_STALE_MS = 2000

const backendUrl = new URL(import.meta.env.VITE_BACKEND_URL || DEFAULT_BACKEND_URL)
const websocketUrl = new URL('/ws/controls', backendUrl)
websocketUrl.protocol = backendUrl.protocol === 'https:' ? 'wss:' : 'ws:'

const connectionState = ref('disconnected')
const lastError = ref('')
const telemetry = ref(null)
const telemetryState = ref('waiting')
const pingMs = ref(null)
const pingState = ref('waiting')
// Operator-settable fields of the send packet, as announced by the backend on
// connect. Padding is already filtered out there. Kept across reconnects so
// Settings stays usable while the backend restarts.
const packetFields = ref([])
// What the backend is recording, pushed on connect and on every change. The
// backend owns this: a recording outlives a UI reconnect, so the renderer
// never asserts it, only reflects it.
const recordingState = ref(null)
// True while the socket is down and the last known recording state may have
// moved on without us.
const recordingStale = ref(false)
// Whether the camera's infrared illuminator is lit, pushed on connect and on
// every change. Like recording, the backend owns it: the light stays on in the
// camera across a UI reload, so the renderer reflects it rather than asserting
// its own idea of it.
const ptzLight = ref(false)
// Stream ids the backend has just re-dialed, each with a count of how often it
// has said so. The backend owns camera transport, so it -- not the settings
// form -- is what knows a live connection has been thrown away, and it says so
// only once the new configuration is actually in force.
const cameraRestarts = ref({})
// Bumped when a config that changes how cameras are dialed leaves here. A feed
// that began connecting at the current value is already negotiating against
// that config, so the acknowledgement of it must not restart it again.
const cameraConfigSeq = ref(0)
// Recording destinations the operator can pick between, read on demand by
// Settings rather than pushed, since it only changes when media is inserted.
const storageTargetsUrl = new URL('/storage/targets', backendUrl).toString()
// The recordings library. Read on demand by the recordings page: it describes
// files at rest, so pushing it would be traffic for something that only changes
// when a recording stops or the operator deletes one.
const recordingsUrl = new URL('/recordings', backendUrl).toString()

// One session, with the per-file detail the list deliberately leaves out.
function recordingSessionUrl(sessionId) {
    return new URL(`/recordings/${encodeURIComponent(sessionId)}`, backendUrl).toString()
}

// A URL for one recorded file. `kind` is the backend's verb: `play` remuxes to
// MP4 on the fly, `download` is the Matroska itself, `thumbnail` is a poster.
function recordingFileUrl(sessionId, filename, kind, params = {}) {
    const path = `/recordings/${encodeURIComponent(sessionId)}/${encodeURIComponent(filename)}/${kind}`
    const url = new URL(path, backendUrl)
    for (const [key, value] of Object.entries(params)) {
        if (value !== null && value !== undefined && value !== false) {
            url.searchParams.set(key, String(value))
        }
    }
    return url.toString()
}

// Receive-only WebRTC signaling for one backend-held camera source. The stream
// id selects which warm source the answer is for; `restart` reports that this
// one stopped delivering pictures, so the backend throws away the connection it
// holds for it and dials the camera again instead of handing out a second peer
// onto a feed that has already stopped.
function cameraSignalingUrl(streamId, restart = false) {
    const url = new URL('/offer', backendUrl)
    if (streamId) url.searchParams.set('src', streamId)
    if (restart) url.searchParams.set('restart', '1')
    return url.toString()
}
let socket = null
let reconnectTimer = null
let telemetryTimer = null
let shouldReconnect = false
let latestConfig = null
let latestPacket = null
let latestPtzRequest = null

function send(message) {
    if (socket?.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(message))
    return true
}

function sendCurrentState() {
    if (latestConfig) send({ type: 'config', config: latestConfig })
    if (latestPacket) send({ type: 'send', packet: latestPacket })
    if (latestPtzRequest !== null) send({ type: 'ptz', ...latestPtzRequest })
}

function clearTelemetry() {
    if (telemetryTimer !== null) {
        clearTimeout(telemetryTimer)
        telemetryTimer = null
    }
    telemetry.value = null
    telemetryState.value = 'waiting'
    pingMs.value = null
    pingState.value = 'waiting'
    // recordingState is deliberately NOT cleared: ffmpeg keeps writing while
    // the socket reconnects, and blanking the HUD would claim otherwise. The
    // backend re-states it on connect.
    recordingStale.value = true
}

function acceptPing(message) {
    const value = message?.ping_ms
    if (value !== null && (!Number.isFinite(value) || value < 0)) {
        console.warn('[backend] Ignored invalid ping message:', message)
        return
    }
    pingMs.value = value
    pingState.value = value === null ? 'unavailable' : 'live'
}

function acceptRecording(message) {
    if (typeof message?.active !== 'boolean' || !Array.isArray(message.sources)) {
        console.warn('[backend] Ignored invalid recording message:', message)
        return
    }
    recordingState.value = message
    recordingStale.value = false
}

function acceptPtzLight(message) {
    if (typeof message?.on !== 'boolean') {
        console.warn('[backend] Ignored invalid PTZ light message:', message)
        return
    }
    ptzLight.value = message.on
}

function acceptCamera(message) {
    const streams = message?.streams
    if (!Array.isArray(streams) || streams.some((id) => typeof id !== 'string')) {
        console.warn('[backend] Ignored invalid camera message:', message)
        return
    }

    const restarts = { ...cameraRestarts.value }
    for (const streamId of streams) restarts[streamId] = (restarts[streamId] ?? 0) + 1
    cameraRestarts.value = restarts
}

function acceptSchema(message) {
    const fields = message?.fields
    if (!Array.isArray(fields) || fields.some((field) => typeof field?.name !== 'string')) {
        console.warn('[backend] Ignored invalid packet schema message:', message)
        return
    }
    packetFields.value = fields
}

function acceptReceive(message) {
    const batteryLevel = message?.packet?.battery_level
    if (!Number.isInteger(batteryLevel) || batteryLevel < 0 || batteryLevel > 100) {
        console.warn('[backend] Ignored invalid telemetry message:', message)
        return
    }

    telemetry.value = { battery_level: batteryLevel }
    telemetryState.value = 'live'
    if (telemetryTimer !== null) clearTimeout(telemetryTimer)
    telemetryTimer = setTimeout(() => {
        telemetryTimer = null
        telemetryState.value = 'stale'
    }, TELEMETRY_STALE_MS)
}

function scheduleReconnect() {
    if (!shouldReconnect || reconnectTimer !== null) return
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        connect()
    }, RECONNECT_DELAY_MS)
}

function connect() {
    shouldReconnect = true
    if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return

    connectionState.value = 'connecting'
    lastError.value = ''
    const nextSocket = new WebSocket(websocketUrl)
    socket = nextSocket

    nextSocket.addEventListener('open', () => {
        if (socket !== nextSocket) return
        connectionState.value = 'connected'
        sendCurrentState()
    })
    nextSocket.addEventListener('message', (event) => {
        if (socket !== nextSocket) return
        try {
            const message = JSON.parse(event.data)
            if (message.type === 'error') {
                lastError.value = message.message || 'Backend rejected a message.'
                console.error('[backend]', lastError.value)
            } else if (message.type === 'receive') {
                acceptReceive(message)
            } else if (message.type === 'ping') {
                acceptPing(message)
            } else if (message.type === 'schema') {
                acceptSchema(message)
            } else if (message.type === 'recording') {
                acceptRecording(message)
            } else if (message.type === 'camera') {
                acceptCamera(message)
            } else if (message.type === 'ptz_light') {
                acceptPtzLight(message)
            }
        } catch (error) {
            console.warn('[backend] Ignored invalid WebSocket response:', error)
        }
    })
    nextSocket.addEventListener('error', () => {
        if (socket === nextSocket) lastError.value = 'Could not connect to the backend.'
    })
    nextSocket.addEventListener('close', () => {
        if (socket !== nextSocket) return
        socket = null
        connectionState.value = 'disconnected'
        clearTelemetry()
        scheduleReconnect()
    })
}

function disconnect() {
    shouldReconnect = false
    if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
    }
    const activeSocket = socket
    socket = null
    activeSocket?.close()
    connectionState.value = 'disconnected'
    clearTelemetry()
}

// The part of a config that decides how a camera is dialed. Everything else in
// a config -- the UDP destination, the ramp rates, where recordings are written
// -- leaves the live connections alone.
function cameraTransportOf(config) {
    return JSON.stringify([
        config?.camera_streams ?? [],
        config?.camera_backend ?? '',
        config?.rtsp_transport ?? '',
    ])
}

let sentCameraTransport = null

function updateConfig(config) {
    latestConfig = { ...config }
    const cameraTransport = cameraTransportOf(latestConfig)
    if (cameraTransport !== sentCameraTransport) {
        sentCameraTransport = cameraTransport
        cameraConfigSeq.value += 1
    }
    send({ type: 'config', config: latestConfig })
}

function updateControl(packet) {
    latestPacket = { ...packet }
    send({ type: 'send', packet: latestPacket })
}

// Camera PTZ request. `direction` is one of 'left' | 'right' | 'up' |
// 'down' while a D-Pad button is held, `zoom` is 'in' | 'out' while RB/LB
// is held, `focus` is 'near' | 'far' while a focus button is held, and all
// are null when nothing is held (the backend then keeps sending the ISAPI
// stop command). The wire contract expects the backend's 'zoom-in'/
// 'zoom-out' and 'focus-near'/'focus-far' vocabulary, so the UI's short
// values are mapped here at the transport boundary rather than leaking
// backend naming into every UI component.
const PTZ_ZOOM_WIRE_VALUES = { in: 'zoom-in', out: 'zoom-out' }
const PTZ_FOCUS_WIRE_VALUES = { near: 'focus-near', far: 'focus-far' }

function updatePtz(direction, zoom, focus, speedMultiplier) {
    const request = {
        direction: direction ?? null,
        zoom: zoom ? PTZ_ZOOM_WIRE_VALUES[zoom] ?? zoom : null,
        focus: focus ? PTZ_FOCUS_WIRE_VALUES[focus] ?? focus : null,
        // The operator's pan/tilt speed step, which the backend multiplies its
        // base speed by. It rides along with the held buttons so a slider moved
        // mid-hold reaches the camera without waiting for a release.
        speed_multiplier: speedMultiplier,
    }
    const previous = latestPtzRequest
        ?? { direction: null, zoom: null, focus: null, speed_multiplier: null }
    if (
        previous.direction === request.direction
        && previous.zoom === request.zoom
        && previous.focus === request.focus
        && previous.speed_multiplier === request.speed_multiplier
    ) return
    latestPtzRequest = request
    send({ type: 'ptz', ...request })
}

// Switch the camera's infrared illuminator on or off. Latched, so it is sent
// once per press rather than while a button is held, and not replayed by
// sendCurrentState(): the backend states the light back on connect, and the
// camera has been holding it all along.
function setPtzLight(on) {
    send({ type: 'ptz_light', on: Boolean(on) })
}

// Start or stop recording every configured camera source. Not replayed by
// sendCurrentState(): the backend is the source of truth for whether a
// recording is running, and replaying a stale intent could stop a live one.
function setRecording(active) {
    send({ type: 'record', action: active ? 'start' : 'stop' })
}

export function useBackendConnection() {
    return {
        connectionState: readonly(connectionState),
        lastError: readonly(lastError),
        telemetry: readonly(telemetry),
        telemetryState: readonly(telemetryState),
        pingMs: readonly(pingMs),
        pingState: readonly(pingState),
        packetFields: readonly(packetFields),
        recordingState: readonly(recordingState),
        recordingStale: readonly(recordingStale),
        cameraRestarts: readonly(cameraRestarts),
        cameraConfigSeq: readonly(cameraConfigSeq),
        cameraSignalingUrl,
        storageTargetsUrl,
        recordingsUrl,
        recordingSessionUrl,
        recordingFileUrl,
        connect,
        disconnect,
        updateConfig,
        updateControl,
        updatePtz,
        ptzLight: readonly(ptzLight),
        setPtzLight,
        setRecording,
    }
}