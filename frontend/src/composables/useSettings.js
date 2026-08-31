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
const maxYVelocity = ref(DEFAULT_MAX_VELOCITY)
const maxThetaVelocity = ref(DEFAULT_MAX_VELOCITY)
const udpHost = ref(DEFAULT_UDP_HOST)
const udpPort = ref(DEFAULT_UDP_PORT)
const udpListenPort = ref(DEFAULT_UDP_LISTEN_PORT)
const useOnScreenKeyboard = ref(true)
let loaded = false
const { updateConfig } = useBackendConnection()

// The active source drives the live feed; the others stay configured but idle.
const activeCamera = computed(() => cameraSources.value[activeCameraIndex.value] ?? EMPTY_CAMERA_SOURCE)
const cameraUrl = computed(() => activeCamera.value.url)
const cameraType = computed(() => activeCamera.value.type)
const cameraUsername = computed(() => activeCamera.value.username)
const cameraPassword = computed(() => activeCamera.value.password)

function buildBackendCameraUrl() {
    const sourceUrl = cameraUrl.value.trim()
    if (!sourceUrl || !sourceUrl.toLowerCase().startsWith('rtsp:')) return sourceUrl

    try {
        const authenticatedUrl = new URL(sourceUrl)
        authenticatedUrl.username = cameraUsername.value
        authenticatedUrl.password = cameraPassword.value
        return authenticatedUrl.toString()
    } catch {
        return sourceUrl
    }
}

function syncBackendConfig() {
    updateConfig({
        udp_host: udpHost.value.trim(),
        udp_port: udpPort.value,
        udp_listen_port: udpListenPort.value,
        // Direct camera WebSockets bypass the backend camera transport.
        camera_url: cameraType.value === 'websocket' ? '' : buildBackendCameraUrl(),
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
        cameraUrl,
        cameraType,
        cameraUsername,
        cameraPassword,
        cameraBackend: readonly(cameraBackend),
        ptzIp: readonly(ptzIp),
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
