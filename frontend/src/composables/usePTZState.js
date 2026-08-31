import { readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// Camera rotation state driven by the shoulder buttons:
//   LB -> left, RB -> right, LT -> down, RT -> up
// The value is null whenever no button is held; the backend then keeps
// sending the ISAPI stop command, so releasing a trigger always stops the
// camera even if a message is lost.
const direction = ref(null)
const { updatePtz } = useBackendConnection()

function publishDirection() {
    updatePtz(direction.value)
}

function setDirection(value) {
    if (direction.value === value) return
    direction.value = value
    publishDirection()
}

function resetDirection() {
    setDirection(null)
}

export function usePTZState() {
    return {
        direction: readonly(direction),
        setDirection,
        resetDirection,
    }
}
