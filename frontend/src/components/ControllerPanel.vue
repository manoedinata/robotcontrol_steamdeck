<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Joystick from './Joystick.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useControlState } from '../composables/useControlState'
import { usePTZState } from '../composables/usePTZState'
import { useDriveMode } from '../composables/useDriveMode'

// Differential-drive controller. The left stick vertical axis drives Y
// velocity and travels up only: which way the robot goes is the drive mode,
// not the side of centre the stick is on. The right stick horizontal axis
// drives theta velocity (right is positive). Outputs are normalized and
// capped to the configurable per-axis limits from Settings. Hardware gamepad
// input is polled each frame and applies a dead zone; pointer/touch input goes
// through the joystick components directly.
const { limitForRole } = useSettings()
const { updatePacket, resetPacket } = useControlState()
const { reset: resetPtz, setDirection, setFocus, setZoom } = usePTZState()
const { reversing } = useDriveMode()
const {
  acquire: acquireGamepad,
  axes: gamepadAxes,
  gamepadName,
  shoulderButtons,
  dpadButtons,
  faceButtons,
} = useGamepad()

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

// Square/X carries both focus directions, told apart by how long it is held:
// a tap steps the focus nearer, holding it sweeps further away. Near is the
// tap because it is the smaller correction of the two -- a subject that has
// drifted out of focus is usually a nudge away, not a sweep.
const FOCUS_HOLD_MS = 300
const FOCUS_TAP_MS = 250
let focusHoldTimer = null
let focusTapTimer = null

function clearFocusTimers() {
  if (focusHoldTimer !== null) clearTimeout(focusHoldTimer)
  if (focusTapTimer !== null) clearTimeout(focusTapTimer)
  focusHoldTimer = null
  focusTapTimer = null
}

watch(() => faceButtons.value.square, (pressed) => {
  if (pressed) {
    // A press during a tap's own step ends that step: the operator is asking
    // for something new, not for more of the last thing.
    if (focusTapTimer !== null) {
      clearTimeout(focusTapTimer)
      focusTapTimer = null
      setFocus(null)
    }
    focusHoldTimer = setTimeout(() => {
      focusHoldTimer = null
      setFocus('far')
    }, FOCUS_HOLD_MS)
    return
  }

  if (focusHoldTimer === null) {
    // The button was held long enough to sweep; releasing stops it.
    setFocus(null)
    return
  }

  // Released before the threshold, so it was a tap: one step nearer.
  clearTimeout(focusHoldTimer)
  focusHoldTimer = null
  setFocus('near')
  focusTapTimer = setTimeout(() => {
    focusTapTimer = null
    setFocus(null)
  }, FOCUS_TAP_MS)
})

// Each axis is scaled by the operator's limit for the packet field that
// carries it: the positive half of the stick reaches `max`, the negative half
// reaches `min`. A range that does not straddle zero would otherwise leave the
// span it covers, so the result is clamped back into it.
function scaleAxis(axis, { min, max }) {
  const scaled = axis >= 0 ? axis * max : -axis * min
  return Math.min(max, Math.max(min, scaled))
}

// The stick's own travel, 0 at centre and 1 at full push, before the mode
// decides its sign. Screen coordinates put up at -1, hence the negation.
const driveTravel = computed(() => -leftStickY.value)
const yVelocity = computed(() => scaleAxis(
  reversing.value ? -driveTravel.value : driveTravel.value,
  limitForRole('yVelocity'),
))
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
      // The hardware stick has a lower half the drive axis does not: pushing
      // it down is no more a reverse request than leaving it centred is.
      leftStickY.value = Math.min(0, applyDeadZone(gamepadAxes.value[1] ?? 0))
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
  clearFocusTimers()
  resetPacket()
  resetPtz()
})
</script>

<template>
  <section class="joystick-section">
    <div class="joysticks-grid">
      <div class="joystick-column">
        <Joystick v-model="leftStickY" axis="vertical" :max="0" label="Left joystick, drive"
          @drag-start="draggedStick = 'left'" @drag-end="draggedStick = null" />
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
