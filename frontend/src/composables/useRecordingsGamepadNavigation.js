import { onBeforeUnmount, onMounted } from 'vue'
import { useGamepad } from './useGamepad'
import { focusInDirection, isDirection } from '../utils/spatialFocus'

const PAGE_SELECTOR = '#recordings-page'
const PLAYER_SELECTOR = '.recording-player'
const CONTROLS = '[data-gamepad-control]:not(:disabled)'

// The recordings page navigates the way Settings does: the D-Pad walks the
// controls spatially and A activates the focused one. It takes a higher
// priority than Settings because the two are never open at once and the page
// must win over the shell's Home handler while it is.
export function useRecordingsGamepadNavigation(cancel = () => { }) {
    const { registerHandler } = useGamepad()
    let unregisterGamepadHandler

    // The player is a dialog over the list, so while it is up the D-Pad must
    // stay inside it. Without this the stick walks off the player and onto the
    // cards behind it, which are still on screen.
    function controls() {
        const scope = document.querySelector(`${PAGE_SELECTOR} ${PLAYER_SELECTOR}`)
            ?? document.querySelector(PAGE_SELECTOR)
        return [...(scope?.querySelectorAll(CONTROLS) ?? [])]
    }

    // A slider is the one control the D-Pad must not simply walk past: left and
    // right are how it is set, the same way Settings gives a <select> its own
    // meaning for A rather than clicking it.
    function nudgeRange(input, action) {
        if (action !== 'left' && action !== 'right') return false
        const max = Number(input.max) || 0
        const step = Math.max(1, Math.round(max / 20))
        const next = Number(input.value) + (action === 'right' ? step : -step)
        input.value = String(Math.min(Math.max(next, Number(input.min) || 0), max))
        input.dispatchEvent(new Event('change', { bubbles: true }))
        return true
    }

    function handleGamepadNavigation(action) {
        const active = document.activeElement
        if (!active?.closest?.(PAGE_SELECTOR)) return false

        if (active instanceof HTMLInputElement && active.type === 'range') {
            if (nudgeRange(active, action)) return true
        }

        if (isDirection(action)) {
            const reachable = controls()
            // Focus may be left behind on a card when the player opens.
            const current = reachable.includes(document.activeElement)
                ? document.activeElement
                : null
            focusInDirection(reachable, current, action, {
                focusOptions: { preventScroll: true },
                scrollIntoView: { block: 'center', behavior: 'smooth' },
            })
            return true
        }
        if (action === 'activate') {
            document.activeElement?.click()
            return true
        }
        if (action === 'cancel') {
            cancel()
            return true
        }
        return false
    }

    onMounted(() => {
        unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 60)
    })

    onBeforeUnmount(() => unregisterGamepadHandler?.())
}
