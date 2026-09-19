import { readonly, ref } from 'vue'

// The Deck's own battery, sampled from the main process rather than pushed the
// way robot telemetry is. It moves by a percent every few minutes, so polling
// costs one small file read and nothing in the HUD is served by knowing sooner.
const POLL_INTERVAL_MS = 20_000

const level = ref(null)
const charging = ref(false)
// 'waiting' until the first read answers, then 'live' or 'unavailable'.
// Unavailable covers the browser preview, where there is no preload bridge,
// and any host without a battery.
const state = ref('waiting')

let started = false

function markUnavailable() {
    level.value = null
    charging.value = false
    state.value = 'unavailable'
}

async function sample() {
    try {
        const battery = await window.electronAPI?.readDeckBattery()
        if (!Number.isFinite(battery?.level)) {
            markUnavailable()
            return
        }
        level.value = battery.level
        charging.value = Boolean(battery.charging)
        state.value = 'live'
    } catch (error) {
        console.error(error)
        markUnavailable()
    }
}

export function useDeckBattery() {
    // Sampled for the life of the app: the HUD is always mounted, so there is
    // no unsubscribe worth writing.
    if (!started) {
        started = true
        sample()
        setInterval(sample, POLL_INTERVAL_MS)
    }

    return {
        deckBatteryLevel: readonly(level),
        deckBatteryCharging: readonly(charging),
        deckBatteryState: readonly(state),
    }
}
