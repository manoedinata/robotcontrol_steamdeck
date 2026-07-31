import { readonly, ref } from 'vue'

// Default velocity cap; mirrors the main-process default so the renderer shows
// a sensible value before settings load and if the bridge is unavailable.
const DEFAULT_MAX_VELOCITY = 10

// Shared reactive settings state. A single module-level instance keeps the
// camera URL and velocity limits in sync across every view without prop
// drilling.
const cameraUrl = ref('')
const maxYVelocity = ref(DEFAULT_MAX_VELOCITY)
const maxThetaVelocity = ref(DEFAULT_MAX_VELOCITY)
const udpHost = ref('')
const udpPort = ref(0)
const useOnScreenKeyboard = ref(true)
let loaded = false

function applySettings(settings) {
    cameraUrl.value = settings?.cameraUrl ?? ''
    maxYVelocity.value = settings?.maxYVelocity ?? DEFAULT_MAX_VELOCITY
    maxThetaVelocity.value = settings?.maxThetaVelocity ?? DEFAULT_MAX_VELOCITY
    udpHost.value = settings?.udpHost ?? ''
    udpPort.value = settings?.udpPort ?? 0
    useOnScreenKeyboard.value = settings?.useOnScreenKeyboard ?? true
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
        maxYVelocity: readonly(maxYVelocity),
        maxThetaVelocity: readonly(maxThetaVelocity),
        udpHost: readonly(udpHost),
        udpPort: readonly(udpPort),
        useOnScreenKeyboard: readonly(useOnScreenKeyboard),
        saveSettings,
        reloadSettings: loadSettings,
    }
}
