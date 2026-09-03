import { computed, readonly, ref, watch } from 'vue'
import { useBackendConnection } from './useBackendConnection'
import { useSettings } from './useSettings'

// Camera PTZ state driven by the gamepad:
//   D-Pad Up/Down/Right/Left -> tilt up / tilt down / pan right / pan left
//   RB -> zoom in, LB -> zoom out
//   Focus Near / Focus Far buttons -> focus near / focus far
// The value is null whenever no input is held; the backend then keeps
// sending the ISAPI stop command, so releasing a button always stops the
// camera even if a message is lost.
//
// Rotation, zoom, and focus are independent axes, so they are tracked
// separately: `direction` is one of 'up' | 'down' | 'left' | 'right' | null,
// `zoom` is 'in' | 'out' | null, and `focus` is 'near' | 'far' | null.
// When multiple D-Pad buttons are held at once the caller picks the winner;
// this store only mirrors what it is told.
const direction = ref(null)
const zoom = ref(null)
const focus = ref(null)
// True while an overlay (Settings, the on-screen keyboard) owns the gamepad:
// there the D-Pad walks the form instead of moving the camera, so the same
// presses must not reach the PTZ endpoint.
const uiOwnsGamepad = ref(false)
const { updatePtz } = useBackendConnection()
const { ptzControlsActiveCamera } = useSettings()

// PTZ requests are only sent while the configured PTZ IP is the camera on
// screen and no overlay is using the gamepad; otherwise a held button would
// move a camera the operator cannot see or is not aiming at.
const ptzAllowed = computed(() => ptzControlsActiveCamera.value && !uiOwnsGamepad.value)

function publish() {
    if (ptzAllowed.value) {
        updatePtz(direction.value, zoom.value, focus.value)
    } else {
        updatePtz(null, null, null)
    }
}

// Switching cameras or opening Settings can make PTZ ineligible mid-hold (and
// hides the focus buttons, so their release is lost). Drop the local state and
// push a stop; the operator re-presses once PTZ is eligible again.
watch(ptzAllowed, (allowed) => {
    if (!allowed) {
        direction.value = null
        zoom.value = null
        focus.value = null
    }
    publish()
})

function setDirection(value) {
    if (direction.value === value) return
    direction.value = value
    publish()
}

function setZoom(value) {
    if (zoom.value === value) return
    zoom.value = value
    publish()
}

function setFocus(value) {
    if (focus.value === value) return
    focus.value = value
    publish()
}

// Called by the shell when an overlay opens or closes. Suppressing publishes
// rather than unmounting the gamepad watchers keeps the D-Pad live for form
// navigation while the camera stays put.
function setUiOwnsGamepad(value) {
    uiOwnsGamepad.value = Boolean(value)
}

function reset() {
    setDirection(null)
    setZoom(null)
    setFocus(null)
}

export function usePTZState() {
    return {
        direction: readonly(direction),
        zoom: readonly(zoom),
        focus: readonly(focus),
        // True only when PTZ commands are allowed for the camera on screen.
        enabled: ptzAllowed,
        setDirection,
        setZoom,
        setFocus,
        setUiOwnsGamepad,
        reset,
    }
}
