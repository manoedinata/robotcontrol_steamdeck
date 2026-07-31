import { readonly, ref } from 'vue'

// Shared reactive settings state. A single module-level instance keeps the
// camera URL in sync across every view without prop drilling.
const cameraUrl = ref('')
let loaded = false

async function loadSettings() {
    try {
        const settings = await window.electronAPI?.loadSettings()
        cameraUrl.value = settings?.cameraUrl ?? ''
    } catch (error) {
        console.error(error)
    } finally {
        loaded = true
    }
}

async function saveSettings(nextSettings) {
    const saved = await window.electronAPI?.saveSettings(nextSettings)
    cameraUrl.value = saved?.cameraUrl ?? nextSettings.cameraUrl?.trim() ?? ''
    return cameraUrl.value
}

export function useSettings() {
    // Load once on first use so any view can trigger initialization.
    if (!loaded) {
        loaded = true
        loadSettings()
    }

    return {
        cameraUrl: readonly(cameraUrl),
        saveSettings,
        reloadSettings: loadSettings,
    }
}
