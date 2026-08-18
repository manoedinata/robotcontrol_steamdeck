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
const streamUrl = new URL('/stream', backendUrl).toString()
let socket = null
let reconnectTimer = null
let telemetryTimer = null
let shouldReconnect = false
let latestConfig = null
let latestPacket = null

function send(message) {
    if (socket?.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(message))
    return true
}

function sendCurrentState() {
    if (latestConfig) send({ type: 'config', config: latestConfig })
    if (latestPacket) send({ type: 'control', packet: latestPacket })
}

function clearTelemetry() {
    if (telemetryTimer !== null) {
        clearTimeout(telemetryTimer)
        telemetryTimer = null
    }
    telemetry.value = null
    telemetryState.value = 'waiting'
}

function acceptTelemetry(message) {
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
            } else if (message.type === 'telemetry') {
                acceptTelemetry(message)
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
    send({ type: 'control', packet: latestPacket })
}

export function useBackendConnection() {
    return {
        connectionState: readonly(connectionState),
        lastError: readonly(lastError),
        telemetry: readonly(telemetry),
        telemetryState: readonly(telemetryState),
        streamUrl,
        connect,
        disconnect,
        updateConfig,
        updateControl,
    }
}