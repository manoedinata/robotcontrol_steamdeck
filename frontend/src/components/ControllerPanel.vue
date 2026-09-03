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
const { limitForRole } = useSettings()
const { updatePacket, resetPacket } = useControlState()
const { reset: resetPtz, setDirection, setZoom } = usePTZState()
const { acquire: acquireGamepad, axes: gamepadAxes, gamepadName, shoulderButtons, dpadButtons } = useGamepad()

const leftStickY = ref(0)
const rightStickX = ref(0)
const draggedStick = ref(null)
let animationFrame
let releaseGamepad

// Camera PTZ mapping: D-Pad moves the camera (up/down tilt, left/right pan)
// and the shoulders zoom (RB zoom-in, LB zoom-out). When several D-Pad
// buttons are held at once the first in this priority order wins, so the
// camera always gets exactly one rotation direction; zoom is independent
// and combines with any rotation.
const PTZ_DPAD_ORDER = ['up', 'down', 'left', 'right']

const ptzDirection = computed(() => {
  const buttons = dpadButtons.value
  if (!buttons?.up && !buttons?.down && !buttons?.left && !buttons?.right) return null
  return PTZ_DPAD_ORDER.find((name) => buttons[name]) ?? null
})

// RB (index 5) zooms in, LB (index 4) zooms out; RB wins if both are held.
const ptzZoom = computed(() => {
  const buttons = shoulderButtons.value
  if (buttons?.rb) return 'in'
  if (buttons?.lb) return 'out'
  return null
})

// Each axis is scaled by the operator's limit for the packet field that
// carries it: the positive half of the stick reaches `max`, the negative half
// reaches `min`. A range that does not straddle zero would otherwise leave the
// span it covers, so the result is clamped back into it.
function scaleAxis(axis, { min, max }) {
  const scaled = axis >= 0 ? axis * max : -axis * min
  return Math.min(max, Math.max(min, scaled))
}

const yVelocity = computed(() => scaleAxis(-leftStickY.value, limitForRole('yVelocity')))
const thetaVelocity = computed(() => scaleAxis(rightStickX.value, limitForRole('thetaVelocity')))

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

// Push the current PTZ request whenever it changes; `updatePtz` sends
// only on transitions, and null (nothing held) triggers the backend
// deadman stop.
watch(ptzDirection, (next) => setDirection(next), { immediate: true })
watch(ptzZoom, (next) => setZoom(next), { immediate: true })

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
  resetPtz()
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
