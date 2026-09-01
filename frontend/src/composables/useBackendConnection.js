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
const signalingUrl = new URL('/offer', backendUrl).toString()
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

function updateConfig(config) {
    latestConfig = { ...config }
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

function updatePtz(direction, zoom, focus) {
    const request = {
        direction: direction ?? null,
        zoom: zoom ? PTZ_ZOOM_WIRE_VALUES[zoom] ?? zoom : null,
        focus: focus ? PTZ_FOCUS_WIRE_VALUES[focus] ?? focus : null,
    }
    const previous = latestPtzRequest ?? { direction: null, zoom: null, focus: null }
    if (
        previous.direction === request.direction
        && previous.zoom === request.zoom
        && previous.focus === request.focus
    ) return
    latestPtzRequest = request
    send({ type: 'ptz', ...request })
}

export function useBackendConnection() {
    return {
        connectionState: readonly(connectionState),
        lastError: readonly(lastError),
        telemetry: readonly(telemetry),
        telemetryState: readonly(telemetryState),
        pingMs: readonly(pingMs),
        pingState: readonly(pingState),
        signalingUrl,
        connect,
        disconnect,
        updateConfig,
        updateControl,
        updatePtz,
    }
}