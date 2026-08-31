import { readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// Camera PTZ state driven by the gamepad:
//   D-Pad Up/Down/Right/Left -> tilt up / tilt down / pan right / pan left
//   RT -> zoom in, LT -> zoom out
// The value is null whenever no input is held; the backend then keeps
// sending the ISAPI stop command, so releasing a button always stops the
// camera even if a message is lost.
//
// Rotation and zoom are independent axes, so they are tracked separately:
// `direction` is one of 'up' | 'down' | 'left' | 'right' | null, and `zoom`
// is 'in' | 'out' | null. When multiple D-Pad buttons are held at once the
// caller picks the winner; this store only mirrors what it is told.
const direction = ref(null)
const zoom = ref(null)
const { updatePtz } = useBackendConnection()

function publish() {
    updatePtz(direction.value, zoom.value)
}

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

function reset() {
    setDirection(null)
    setZoom(null)
}

export function usePTZState() {
    return {
        direction: readonly(direction),
        zoom: readonly(zoom),
        setDirection,
        setZoom,
        reset,
    }
}
