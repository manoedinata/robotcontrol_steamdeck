import { nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useGamepad } from './useGamepad'
import { focusInDirection, isDirection } from '../utils/spatialFocus'

const PAGE_SELECTOR = '#settings-page'
const CONTROL_SELECTOR = `${PAGE_SELECTOR} [data-gamepad-control]:not(:disabled)`

export function useSettingsGamepadNavigation(activeKeyboard) {
    const { registerHandler } = useGamepad()
    let unregisterGamepadHandler

    function controls() {
        return [...document.querySelectorAll(CONTROL_SELECTOR)]
    }

    function activateControl(control) {
        if (control instanceof HTMLSelectElement) {
            const nextIndex = (control.selectedIndex + 1) % control.options.length
            control.selectedIndex = nextIndex
            control.dispatchEvent(new Event('change', { bubbles: true }))
            return
        }
        control?.click()
    }

    function handleGamepadNavigation(action) {
        if (activeKeyboard.value || !document.activeElement?.closest?.(PAGE_SELECTOR)) return false

        if (isDirection(action)) {
            focusInDirection(controls(), document.activeElement, action, {
                focusOptions: { preventScroll: true },
                scrollIntoView: { block: 'center', behavior: 'smooth' },
            })
            return true
        }
        if (action === 'activate') {
            activateControl(document.activeElement)
            return true
        }
        if (action === 'cancel') {
            document.querySelector('.sidebar [data-route-name="settings"]')?.focus()
            return true
        }
        return false
    }

    async function focusSaveControl() {
        await nextTick()
        document.querySelector(`${PAGE_SELECTOR} [data-gamepad-save]`)?.focus({ preventScroll: true })
    }

    onMounted(() => {
        unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 50)
    })

    onBeforeUnmount(() => unregisterGamepadHandler?.())

    return { focusSaveControl }
}