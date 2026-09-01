import { computed, readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// Default velocity cap; mirrors the main-process default so the renderer shows
// a sensible value before settings load and if the bridge is unavailable.
const DEFAULT_MAX_VELOCITY = 10
const DEFAULT_UDP_HOST = '127.0.0.1'
const DEFAULT_UDP_PORT = 8888
const DEFAULT_UDP_LISTEN_PORT = 8889
const DEFAULT_CAMERA_BACKEND = 'go2rtc'
const DEFAULT_CAMERA_TYPE = 'rtsp'
const DEFAULT_PTZ_IP = ''
const DEFAULT_PTZ_USERNAME = ''
const DEFAULT_PTZ_PASSWORD = ''
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
const ptzUsername = ref(DEFAULT_PTZ_USERNAME)
const ptzPassword = ref(DEFAULT_PTZ_PASSWORD)
const maxYVelocity = ref(DEFAULT_MAX_VELOCITY)
const maxThetaVelocity = ref(DEFAULT_MAX_VELOCITY)
const udpHost = ref(DEFAULT_UDP_HOST)
const udpPort = ref(DEFAULT_UDP_PORT)
const udpListenPort = ref(DEFAULT_UDP_LISTEN_PORT)
const useOnScreenKeyboard = ref(true)
let loaded = false
const { updateConfig } = useBackendConnection()

// Every configured source stays connected at once so switching is instant;
// `activeCameraIndex` only decides which warm feed is shown. `cameraUrl` is
// kept for the Home HUD's address readout.
const activeCamera = computed(() => cameraSources.value[activeCameraIndex.value] ?? EMPTY_CAMERA_SOURCE)
const cameraUrl = computed(() => activeCamera.value.url)

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
        ptz_username: ptzUsername.value,
        ptz_password: ptzPassword.value,
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
    ptzUsername.value = typeof settings?.ptzUsername === 'string' ? settings.ptzUsername : DEFAULT_PTZ_USERNAME
    ptzPassword.value = typeof settings?.ptzPassword === 'string' ? settings.ptzPassword : DEFAULT_PTZ_PASSWORD
    maxYVelocity.value = settings?.maxYVelocity ?? DEFAULT_MAX_VELOCITY
    maxThetaVelocity.value = settings?.maxThetaVelocity ?? DEFAULT_MAX_VELOCITY
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
        ptzUsername: readonly(ptzUsername),
        ptzPassword: readonly(ptzPassword),
        maxYVelocity: readonly(maxYVelocity),
        maxThetaVelocity: readonly(maxThetaVelocity),
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
