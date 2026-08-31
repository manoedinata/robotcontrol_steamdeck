<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Joystick from './Joystick.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useControlState } from '../composables/useControlState'
import { usePTZState } from '../composables/usePTZState'

// Differential-drive controller. The left stick vertical axis drives Y
// velocity (up is positive), the right stick horizontal axis drives theta
// velocity (right is positive). Outputs are normalized and capped to the
// configurable per-axis limits from Settings. Hardware gamepad input is polled
// each frame and applies a dead zone; pointer/touch input goes through the
// joystick components directly.
const { maxYVelocity, maxThetaVelocity } = useSettings()
const { updatePacket, resetPacket } = useControlState()
const { resetDirection: resetPtzDirection, setDirection } = usePTZState()
const { acquire: acquireGamepad, axes: gamepadAxes, gamepadName, shoulderButtons } = useGamepad()

const leftStickY = ref(0)
const rightStickX = ref(0)
const draggedStick = ref(null)
let animationFrame
let releaseGamepad

// Camera rotation mapping: LB -> left, RB -> right, LT -> down, RT -> up.
// When multiple buttons are held at once the first in this priority order
// wins, so the camera always gets exactly one direction.
const PTZ_BUTTON_DIRECTIONS = { lb: 'left', rb: 'right', lt: 'down', rt: 'up' }
const PTZ_BUTTON_ORDER = ['rt', 'lt', 'rb', 'lb']

const ptzDirection = computed(() => {
  const buttons = shoulderButtons.value
  if (!buttons?.lb && !buttons?.rb && !buttons?.lt && !buttons?.rt) return null
  return PTZ_BUTTON_DIRECTIONS[PTZ_BUTTON_ORDER.find((name) => buttons[name])]
})

const yVelocity = computed(() => -leftStickY.value * maxYVelocity.value)
const thetaVelocity = computed(() => rightStickX.value * maxThetaVelocity.value)

function formatVelocity(value) {
  const rounded = Math.round(value * 10) / 10
  return Object.is(rounded, -0) ? '0.0' : rounded.toFixed(1)
}

function applyDeadZone(value, threshold = 0.12) {
  if (Math.abs(value) < threshold) return 0
  return Math.sign(value) * ((Math.abs(value) - threshold) / (1 - threshold))
}

function publishVelocity() {
  const y = Math.round(yVelocity.value)
  const theta = Math.round(thetaVelocity.value)
  updatePacket({
    pwm: y,
    // Reversing while driving backwards keeps steering relative to the driver's
    // view instead of the robot's heading.
    steering: y < 0 ? -theta : theta,
  })
}

watch([yVelocity, thetaVelocity], publishVelocity, { immediate: true })

// Push the current rotation request whenever it changes; `updatePtz` sends
// only on transitions, and null (all triggers released) triggers the backend
// deadman stop.
watch(ptzDirection, (next) => setDirection(next), { immediate: true })

function updateGamepad() {
  if (gamepadName.value) {
    if (draggedStick.value !== 'left') {
      leftStickY.value = applyDeadZone(gamepadAxes.value[1] ?? 0)
    }
    if (draggedStick.value !== 'right') {
      rightStickX.value = applyDeadZone(gamepadAxes.value[2] ?? 0)
    }
  } else {
    if (draggedStick.value !== 'left') {
      leftStickY.value = 0
    }
    if (draggedStick.value !== 'right') {
      rightStickX.value = 0
    }
  }

  animationFrame = requestAnimationFrame(updateGamepad)
}

onMounted(() => {
  releaseGamepad = acquireGamepad()
  animationFrame = requestAnimationFrame(updateGamepad)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
  releaseGamepad?.()
  resetPacket()
  resetPtzDirection()
})
</script>

<template>
  <section class="joystick-section">
    <div class="joysticks-grid">
      <div class="joystick-column">
        <Joystick v-model="leftStickY" axis="vertical" label="Left joystick" @drag-start="draggedStick = 'left'"
          @drag-end="draggedStick = null" />
        <p class="joystick-readout">
          <span>Linear</span>
          <strong>{{ formatVelocity(yVelocity) }}</strong>
        </p>
      </div>

      <div class="joystick-column">
        <Joystick v-model="rightStickX" axis="horizontal" label="Right joystick" @drag-start="draggedStick = 'right'"
          @drag-end="draggedStick = null" />
        <p class="joystick-readout">
          <span>Angular</span>
          <strong>{{ formatVelocity(thetaVelocity) }}</strong>
        </p>
      </div>
    </div>
  </section>
</template>
