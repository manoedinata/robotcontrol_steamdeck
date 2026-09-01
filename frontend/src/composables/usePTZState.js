import { readonly, ref, watch } from 'vue'
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
const { updatePtz } = useBackendConnection()
const { ptzControlsActiveCamera } = useSettings()

// PTZ requests are only sent while the configured PTZ IP is the camera on
// screen; otherwise a held button would move a camera the operator cannot
// see.
function publish() {
    if (ptzControlsActiveCamera.value) {
        updatePtz(direction.value, zoom.value, focus.value)
    } else {
        updatePtz(null, null, null)
    }
}

// Switching cameras can make PTZ ineligible mid-hold (and hides the focus
// buttons, so their release is lost). Drop the local state and push a stop;
// the operator re-presses once the matching camera is back on screen.
watch(ptzControlsActiveCamera, (allowed) => {
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
        enabled: ptzControlsActiveCamera,
        setDirection,
        setZoom,
        setFocus,
        reset,
    }
}
