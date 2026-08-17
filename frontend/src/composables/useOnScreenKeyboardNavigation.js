import { nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useGamepad } from './useGamepad'
import { focusInDirection, isDirection } from '../utils/spatialFocus'

export function useOnScreenKeyboardNavigation(keyboard, cancel) {
    const { registerHandler } = useGamepad()
    let unregisterGamepadHandler

    function handleWindowKeydown(event) {
        if (event.key !== 'Escape') return
        event.preventDefault()
        cancel()
    }

    function handleGamepadNavigation(action) {
        const direction = action.replace('stick-', '')
        if (isDirection(direction)) {
            const buttons = [...keyboard.value.querySelectorAll('button:not(:disabled)')]
            focusInDirection(buttons, document.activeElement, direction)
        } else if (action === 'activate') {
            document.activeElement?.click()
        } else if (action === 'cancel') {
            cancel()
        }
        return true
    }

    onMounted(async () => {
        window.addEventListener('keydown', handleWindowKeydown)
        await nextTick()
        keyboard.value?.querySelector('[data-osk-key]')?.focus()
        unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 100)
    })

    onBeforeUnmount(() => {
        window.removeEventListener('keydown', handleWindowKeydown)
        unregisterGamepadHandler?.()
    })
}