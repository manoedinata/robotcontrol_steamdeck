import { readonly, ref } from 'vue'

const gamepadName = ref('')
const axes = ref([0, 0, 0, 0])
// Live shoulder button state, polled each frame. Standard mapping: 4 = LB,
// 5 = RB, 6 = LT, 7 = RT. Triggers on many pads report as analog values
// instead of booleans, so a small threshold marks them "pressed".
const shoulderButtons = ref({ lb: false, rb: false, lt: false, rt: false })
// Live D-pad state, polled each frame. Standard mapping: 12 = up, 13 = down,
// 14 = left, 15 = right. Used for camera rotation, so it must stay separate
// from the navigation `direction` events that the same buttons also emit.
const dpadButtons = ref({ up: false, down: false, left: false, right: false })
const handlers = new Set()

const DIRECTION_REPEAT_DELAY = 360
const DIRECTION_REPEAT_INTERVAL = 120
const STICK_NAVIGATION_THRESHOLD = 0.55
const TRIGGER_PRESSED_THRESHOLD = 0.35

let animationFrame = 0
let consumerCount = 0
let previousButtons = []
let heldDirection = null
let nextDirectionAt = 0

function dispatch(action) {
    const orderedHandlers = [...handlers].sort((a, b) => b.priority - a.priority)
    for (const entry of orderedHandlers) {
        if (entry.handler(action)) return
    }
}

// Analog triggers report a value in 0..1 with `pressed` only set once they hit
// the hardware's own click point; reading the value catches lighter presses.
function buttonPressed(gamepad, index) {
    const button = gamepad.buttons[index]
    if (!button) return false
    return Boolean(button.pressed) || (button.value ?? 0) >= TRIGGER_PRESSED_THRESHOLD
}

function currentDirection(gamepad) {
    if (gamepad.buttons[12]?.pressed) return 'up'
    if (gamepad.buttons[13]?.pressed) return 'down'
    if (gamepad.buttons[14]?.pressed) return 'left'
    if (gamepad.buttons[15]?.pressed) return 'right'
    if ((gamepad.axes[1] ?? 0) < -STICK_NAVIGATION_THRESHOLD) return 'stick-up'
    if ((gamepad.axes[1] ?? 0) > STICK_NAVIGATION_THRESHOLD) return 'stick-down'
    if ((gamepad.axes[0] ?? 0) < -STICK_NAVIGATION_THRESHOLD) return 'stick-left'
    if ((gamepad.axes[0] ?? 0) > STICK_NAVIGATION_THRESHOLD) return 'stick-right'
    return null
}

function pollGamepad(timestamp) {
    const gamepad = Array.from(navigator.getGamepads?.() ?? []).find(Boolean)

    if (!gamepad) {
        gamepadName.value = ''
        axes.value = [0, 0, 0, 0]
        shoulderButtons.value = { lb: false, rb: false, lt: false, rt: false }
        dpadButtons.value = { up: false, down: false, left: false, right: false }
        previousButtons = []
        heldDirection = null
        animationFrame = requestAnimationFrame(pollGamepad)
        return
    }

    gamepadName.value = gamepad.id
    axes.value = [0, 1, 2, 3].map((index) => gamepad.axes[index] ?? 0)

    shoulderButtons.value = {
        lb: Boolean(gamepad.buttons[4]?.pressed),
        rb: Boolean(gamepad.buttons[5]?.pressed),
        lt: buttonPressed(gamepad, 6),
        rt: buttonPressed(gamepad, 7),
    }

    dpadButtons.value = {
        up: Boolean(gamepad.buttons[12]?.pressed),
        down: Boolean(gamepad.buttons[13]?.pressed),
        left: Boolean(gamepad.buttons[14]?.pressed),
        right: Boolean(gamepad.buttons[15]?.pressed),
    }

    const direction = currentDirection(gamepad)
    if (direction !== heldDirection) {
        heldDirection = direction
        if (direction) {
            dispatch(direction)
            nextDirectionAt = timestamp + DIRECTION_REPEAT_DELAY
        }
    } else if (direction && timestamp >= nextDirectionAt) {
        dispatch(direction)
        nextDirectionAt = timestamp + DIRECTION_REPEAT_INTERVAL
    }

    const buttons = gamepad.buttons.map((button) => button.pressed)
    if (buttons[0] && !previousButtons[0]) dispatch('activate')
    if (buttons[1] && !previousButtons[1]) dispatch('cancel')
    previousButtons = buttons

    animationFrame = requestAnimationFrame(pollGamepad)
}

function startPolling() {
    if (!animationFrame) animationFrame = requestAnimationFrame(pollGamepad)
}

function stopPolling() {
    if (consumerCount > 0 || !animationFrame) return
    cancelAnimationFrame(animationFrame)
    animationFrame = 0
    gamepadName.value = ''
    axes.value = [0, 0, 0, 0]
    shoulderButtons.value = { lb: false, rb: false, lt: false, rt: false }
    dpadButtons.value = { up: false, down: false, left: false, right: false }
    previousButtons = []
    heldDirection = null
}

export function useGamepad() {
    function acquire() {
        consumerCount += 1
        startPolling()

        let released = false
        return () => {
            if (released) return
            released = true
            consumerCount = Math.max(0, consumerCount - 1)
            stopPolling()
        }
    }

    function registerHandler(handler, priority = 0) {
        const entry = { handler, priority }
        handlers.add(entry)
        const release = acquire()

        return () => {
            handlers.delete(entry)
            release()
        }
    }

    return {
        axes: readonly(axes),
        gamepadName: readonly(gamepadName),
        shoulderButtons: readonly(shoulderButtons),
        dpadButtons: readonly(dpadButtons),
        acquire,
        registerHandler,
    }
}