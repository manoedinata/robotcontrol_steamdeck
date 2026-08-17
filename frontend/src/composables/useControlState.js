import { readonly, reactive, watch } from 'vue'
import { useBackendConnection } from './useBackendConnection'

const packet = reactive({
    vy: 0,
    vtheta: 0,
})
let publishFrame = null

const { updateControl } = useBackendConnection()

watch(packet, () => {
    if (publishFrame !== null) return
    publishFrame = requestAnimationFrame(() => {
        publishFrame = null
        updateControl(packet)
    })
}, { deep: true, immediate: true })

function updatePacket(values) {
    Object.assign(packet, values)
}

function resetPacket() {
    for (const name of Object.keys(packet)) packet[name] = 0
}

export function useControlState() {
    return {
        packet: readonly(packet),
        updatePacket,
        resetPacket,
    }
}