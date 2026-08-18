import { readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// Default velocity cap; mirrors the main-process default so the renderer shows
// a sensible value before settings load and if the bridge is unavailable.
const DEFAULT_MAX_VELOCITY = 10
const DEFAULT_UDP_LISTEN_PORT = 8889

// Shared reactive settings state. A single module-level instance keeps the
// camera URL and velocity limits in sync across every view without prop
// drilling.
const cameraUrl = ref('')
const cameraUsername = ref('')
const cameraPassword = ref('')
const maxYVelocity = ref(DEFAULT_MAX_VELOCITY)
const maxThetaVelocity = ref(DEFAULT_MAX_VELOCITY)
const udpHost = ref('')
const udpPort = ref(0)
const udpListenPort = ref(DEFAULT_UDP_LISTEN_PORT)
const useOnScreenKeyboard = ref(true)
let loaded = false
const { updateConfig } = useBackendConnection()

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
        camera_url: buildBackendCameraUrl(),
    })
}

function parseStoredCameraSettings(settings) {
    const sourceUrl = settings?.cameraUrl ?? ''
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

    return {
        url: normalizedUrl,
        username: settings?.cameraUsername ?? embeddedUsername,
        password: settings?.cameraPassword ?? embeddedPassword,
    }
}

function applySettings(settings) {
    const cameraSettings = parseStoredCameraSettings(settings)
    cameraUrl.value = cameraSettings.url
    cameraUsername.value = cameraSettings.username
    cameraPassword.value = cameraSettings.password
    maxYVelocity.value = settings?.maxYVelocity ?? DEFAULT_MAX_VELOCITY
    maxThetaVelocity.value = settings?.maxThetaVelocity ?? DEFAULT_MAX_VELOCITY
    udpHost.value = settings?.udpHost ?? ''
    udpPort.value = settings?.udpPort ?? 0
    udpListenPort.value = settings?.udpListenPort ?? DEFAULT_UDP_LISTEN_PORT
    useOnScreenKeyboard.value = settings?.useOnScreenKeyboard ?? true
    syncBackendConfig()
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
        cameraUrl: readonly(cameraUrl),
        cameraUsername: readonly(cameraUsername),
        cameraPassword: readonly(cameraPassword),
        maxYVelocity: readonly(maxYVelocity),
        maxThetaVelocity: readonly(maxThetaVelocity),
        udpHost: readonly(udpHost),
        udpPort: readonly(udpPort),
        udpListenPort: readonly(udpListenPort),
        useOnScreenKeyboard: readonly(useOnScreenKeyboard),
        saveSettings,
        reloadSettings: loadSettings,
    }
}
