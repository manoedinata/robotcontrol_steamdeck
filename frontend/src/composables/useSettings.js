import { computed, readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// Fallback cap used only before the backend announces the send-packet layout;
// no packet can be sent in that state anyway.
const DEFAULT_MAX_VELOCITY = 10
const DEFAULT_LIMIT = Object.freeze({ min: -DEFAULT_MAX_VELOCITY, max: DEFAULT_MAX_VELOCITY })
const DEFAULT_UDP_HOST = '127.0.0.1'
const DEFAULT_UDP_PORT = 8888
const DEFAULT_UDP_LISTEN_PORT = 8889
const DEFAULT_CAMERA_BACKEND = 'go2rtc'
const DEFAULT_CAMERA_TYPE = 'rtsp'
const DEFAULT_PTZ_IP = ''
const EMPTY_CAMERA_SOURCE = Object.freeze({
    url: '',
    type: DEFAULT_CAMERA_TYPE,
    username: '',
    password: '',
})

// Shared reactive settings state. A single module-level instance keeps the
// camera sources and velocity limits in sync across every view without prop
// drilling.
const cameraSources = ref([{ ...EMPTY_CAMERA_SOURCE }])
const activeCameraIndex = ref(0)
const cameraBackend = ref(DEFAULT_CAMERA_BACKEND)
const ptzIp = ref(DEFAULT_PTZ_IP)
// Operator overrides for the send packet's field bounds, keyed by field name:
// `{ pwm: { min, max } }`. A field with no entry keeps the schema's own bounds.
const packetLimits = ref({})
// Files written before per-field limits stored one symmetric cap per axis;
// these seed the limits of the roles they used to cap.
const legacyRoleLimits = ref({})
const udpHost = ref(DEFAULT_UDP_HOST)
const udpPort = ref(DEFAULT_UDP_PORT)
const udpListenPort = ref(DEFAULT_UDP_LISTEN_PORT)
const useOnScreenKeyboard = ref(true)
let loaded = false
const { updateConfig, packetFields } = useBackendConnection()

// Every configured source stays connected at once so switching is instant;
// `activeCameraIndex` only decides which warm feed is shown. `cameraUrl` is
// kept for the Home HUD's address readout.
const activeCamera = computed(() => cameraSources.value[activeCameraIndex.value] ?? EMPTY_CAMERA_SOURCE)
const cameraUrl = computed(() => activeCamera.value.url)

// Host of a bare IP, `host:port`, or a full URL; '' when it cannot be read.
function hostOf(value) {
    const raw = (value ?? '').trim()
    if (!raw) return ''
    try {
        return new URL(raw.includes('://') ? raw : `http://${raw}`).hostname
    } catch {
        return raw.split('/')[0].split(':')[0]
    }
}

// PTZ pan/tilt/zoom/focus only make sense for the camera on screen: true only
// when a PTZ IP is set and it is the host of the active camera stream.
const ptzControlsActiveCamera = computed(() => {
    const ptzHost = hostOf(ptzIp.value)
    return Boolean(ptzHost) && hostOf(activeCamera.value.url) === ptzHost
})

function clampToSchema(value, field) {
    let result = value
    if (Number.isFinite(field.min)) result = Math.max(field.min, result)
    if (Number.isFinite(field.max)) result = Math.min(field.max, result)
    return result
}

// One entry per operator-settable send field, in schema order: the effective
// min/max plus the schema bounds they are clamped into. The backend announces
// the fields (padding excluded) on connect, so this is empty until then.
const packetFieldLimits = computed(() => packetFields.value.map((field) => {
    const stored = packetLimits.value[field.name] ?? legacyRoleLimits.value[field.role]
    const range = stored ?? {
        min: Number.isFinite(field.min) ? field.min : -DEFAULT_MAX_VELOCITY,
        max: Number.isFinite(field.max) ? field.max : DEFAULT_MAX_VELOCITY,
    }

    return {
        name: field.name,
        role: field.role ?? field.name,
        type: field.type,
        // Null means the schema leaves that side unbounded.
        schemaMin: Number.isFinite(field.min) ? field.min : null,
        schemaMax: Number.isFinite(field.max) ? field.max : null,
        min: clampToSchema(range.min, field),
        max: clampToSchema(range.max, field),
    }
}))

// The joysticks address fields by role so ControllerPanel never needs a field
// name. Falls back to the legacy cap until the layout arrives.
function limitForRole(role) {
    const field = packetFieldLimits.value.find((entry) => entry.role === role)
    if (field) return { min: field.min, max: field.max }
    return legacyRoleLimits.value[role] ?? DEFAULT_LIMIT
}

// A stream id is stable per source slot and is how the renderer addresses a
// backend-dialed RTSP stream (`POST /offer?src=<id>`).
function cameraStreamId(index) {
    return `cam-${index}`
}

function buildBackendCameraUrl(source) {
    const sourceUrl = (source?.url ?? '').trim()
    if (!sourceUrl || !sourceUrl.toLowerCase().startsWith('rtsp:')) return sourceUrl

    try {
        const authenticatedUrl = new URL(sourceUrl)
        authenticatedUrl.username = source.username ?? ''
        authenticatedUrl.password = source.password ?? ''
        return authenticatedUrl.toString()
    } catch {
        return sourceUrl
    }
}

// Per-source descriptor the Home view renders one warm CameraFeed from.
const cameraFeeds = computed(() => cameraSources.value.map((source, index) => {
    const url = (source.url ?? '').trim()
    return {
        // Stable per slot so HUD status tracking survives a remount.
        id: cameraStreamId(index),
        // Changes when the source is edited so Vue remounts (reconnects)
        // that one feed; unchanged when only the active source switches.
        key: `${cameraStreamId(index)}:${source.type}:${url}`,
        index,
        type: source.type,
        // Empty until the source is actually configured, so an unconfigured
        // slot shows "idle" instead of retrying against the backend.
        streamId: source.type === 'rtsp' && url ? cameraStreamId(index) : '',
        // Only WebSocket sources are reached directly by the renderer.
        wsUrl: source.type === 'websocket' ? url : '',
    }
}))

function syncBackendConfig() {
    // Send every RTSP source so the backend keeps them all warm. WebSocket
    // sources bypass the backend camera transport entirely.
    const cameraStreams = cameraSources.value
        .map((source, index) => ({ source, index }))
        .filter(({ source }) => source.type === 'rtsp' && (source.url ?? '').trim())
        .map(({ source, index }) => ({
            id: cameraStreamId(index),
            url: buildBackendCameraUrl(source),
        }))

    updateConfig({
        udp_host: udpHost.value.trim(),
        udp_port: udpPort.value,
        udp_listen_port: udpListenPort.value,
        camera_streams: cameraStreams,
        camera_backend: cameraBackend.value,
        ptz_ip: ptzIp.value.trim(),
    })
}

// Accepts both the per-source shape and the legacy top-level single-camera keys
// so existing settings.json files keep working.
function normalizeCameraSource(source) {
    const sourceUrl = source?.url ?? source?.cameraUrl ?? ''
    let normalizedUrl = sourceUrl
    let embeddedUsername = ''
    let embeddedPassword = ''

    try {
        const parsedUrl = new URL(sourceUrl)
        embeddedUsername = decodeURIComponent(parsedUrl.username)
        embeddedPassword = decodeURIComponent(parsedUrl.password)
        parsedUrl.username = ''
        parsedUrl.password = ''
        normalizedUrl = parsedUrl.toString()
    } catch {
        // Keep malformed legacy values visible in Settings for correction.
    }

    const storedType = source?.type
        ?? source?.cameraType
        ?? (sourceUrl.toLowerCase().startsWith('ws:') ? 'websocket' : DEFAULT_CAMERA_TYPE)

    return {
        url: normalizedUrl,
        type: storedType === 'websocket' ? 'websocket' : DEFAULT_CAMERA_TYPE,
        username: source?.username ?? source?.cameraUsername ?? embeddedUsername,
        password: source?.password ?? source?.cameraPassword ?? embeddedPassword,
    }
}

function parseStoredCameraSources(settings) {
    const stored = Array.isArray(settings?.cameraSources) && settings.cameraSources.length
        ? settings.cameraSources
        // Legacy shape: a single camera in top-level cameraUrl/cameraType keys.
        : [settings ?? {}]

    return stored.map(normalizeCameraSource)
}

function parsePacketLimits(value) {
    if (!value || typeof value !== 'object') return {}

    const limits = {}
    for (const [name, range] of Object.entries(value)) {
        const min = Number(range?.min)
        const max = Number(range?.max)
        if (Number.isFinite(min) && Number.isFinite(max)) limits[name] = { min, max }
    }
    return limits
}

// Legacy shape: one symmetric cap per axis under `maxYVelocity`/`maxThetaVelocity`.
function parseLegacyRoleLimits(settings) {
    const legacy = {}
    for (const [key, role] of [['maxYVelocity', 'yVelocity'], ['maxThetaVelocity', 'thetaVelocity']]) {
        const cap = Number(settings?.[key])
        if (Number.isFinite(cap) && cap > 0) legacy[role] = { min: -cap, max: cap }
    }
    return legacy
}

function clampCameraIndex(index) {
    const count = cameraSources.value.length
    if (!count) return 0
    return Math.max(0, Math.min(count - 1, Number.isInteger(index) ? index : 0))
}

function applySettings(settings) {
    cameraSources.value = parseStoredCameraSources(settings)
    activeCameraIndex.value = clampCameraIndex(settings?.activeCameraIndex ?? 0)
    cameraBackend.value = settings?.cameraBackend ?? DEFAULT_CAMERA_BACKEND
    ptzIp.value = typeof settings?.ptzIp === 'string' ? settings.ptzIp : DEFAULT_PTZ_IP
    packetLimits.value = parsePacketLimits(settings?.packetLimits)
    legacyRoleLimits.value = parseLegacyRoleLimits(settings)
    udpHost.value = settings?.udpHost ?? DEFAULT_UDP_HOST
    udpPort.value = settings?.udpPort ?? DEFAULT_UDP_PORT
    udpListenPort.value = settings?.udpListenPort ?? DEFAULT_UDP_LISTEN_PORT
    useOnScreenKeyboard.value = settings?.useOnScreenKeyboard ?? true
    syncBackendConfig()
}

function selectCamera(index) {
    const count = cameraSources.value.length
    if (!count) return
    // Wrap so repeated Circle presses cycle through every configured source.
    activeCameraIndex.value = ((index % count) + count) % count
    syncBackendConfig()
}

function switchCamera(offset = 1) {
    selectCamera(activeCameraIndex.value + offset)
}

async function loadSettings() {
    try {
        const settings = await window.electronAPI?.loadSettings()
        applySettings(settings)
    } catch (error) {
        console.error(error)
    } finally {
        loaded = true
    }
}

async function saveSettings(nextSettings) {
    const saved = await window.electronAPI?.saveSettings(nextSettings)
    applySettings(saved ?? nextSettings)
    return saved
}

export function useSettings() {
    // Load once on first use so any view can trigger initialization.
    if (!loaded) {
        loaded = true
        loadSettings()
    }

    return {
        cameraSources: readonly(cameraSources),
        activeCameraIndex: readonly(activeCameraIndex),
        cameraFeeds,
        cameraUrl,
        cameraBackend: readonly(cameraBackend),
        ptzIp: readonly(ptzIp),
        ptzControlsActiveCamera,
        packetFieldLimits,
        packetLimits: readonly(packetLimits),
        limitForRole,
        udpHost: readonly(udpHost),
        udpPort: readonly(udpPort),
        udpListenPort: readonly(udpListenPort),
        useOnScreenKeyboard: readonly(useOnScreenKeyboard),
        selectCamera,
        switchCamera,
        saveSettings,
        reloadSettings: loadSettings,
    }
}
