<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Joystick from './Joystick.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'

// Differential-drive controller. The left stick vertical axis drives Y
// velocity (up is positive), the right stick horizontal axis drives theta
// velocity (right is positive). Outputs are normalized and capped to the
// configurable per-axis limits from Settings. Hardware gamepad input is polled
// each frame and applies a dead zone; pointer/touch input goes through the
// joystick components directly.
const { maxYVelocity, maxThetaVelocity, udpHost, udpPort } = useSettings()
const { acquire: acquireGamepad, axes: gamepadAxes, gamepadName } = useGamepad()

const leftStickY = ref(0)
const rightStickX = ref(0)
const draggedStick = ref(null)
let animationFrame
let releaseGamepad

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

function hasUdpDestination() {
  return udpHost.value.trim().length > 0
    && Number.isInteger(udpPort.value)
    && udpPort.value >= 1
    && udpPort.value <= 65535
}

function publishVelocity() {
  if (!hasUdpDestination()) {
    window.electronAPI?.stopUdpVelocity?.()
    return
  }

  window.electronAPI?.updateUdpVelocity?.(
    udpHost.value.trim(),
    udpPort.value,
    [yVelocity.value, thetaVelocity.value],
  )
}

watch([yVelocity, thetaVelocity, udpHost, udpPort], publishVelocity, { immediate: true })

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
  window.electronAPI?.stopUdpVelocity?.()
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
