<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Gamepad2 } from '@lucide/vue'
import Joystick from './Joystick.vue'

// Differential-drive controller. The left stick vertical axis drives Y
// velocity (up is positive), the right stick horizontal axis drives theta
// velocity (right is positive). Both outputs are normalized and capped to
// -10..+10. Hardware gamepad input is polled each frame and applies a dead
// zone; pointer/touch input goes through the joystick components directly.
const leftStickY = ref(0)
const rightStickX = ref(0)
const draggedStick = ref(null)
const gamepadName = ref('')
let animationFrame

const yVelocity = computed(() => -leftStickY.value * 10)
const thetaVelocity = computed(() => rightStickX.value * 10)

function formatVelocity(value) {
  const rounded = Math.round(value * 10) / 10
  return Object.is(rounded, -0) ? '0.0' : rounded.toFixed(1)
}

function applyDeadZone(value, threshold = 0.12) {
  if (Math.abs(value) < threshold) return 0
  return Math.sign(value) * ((Math.abs(value) - threshold) / (1 - threshold))
}

function updateGamepad() {
  const gamepad = Array.from(navigator.getGamepads?.() ?? []).find(Boolean)

  if (gamepad) {
    gamepadName.value = gamepad.id

    if (draggedStick.value !== 'left') {
      leftStickY.value = applyDeadZone(gamepad.axes[1] ?? 0)
    }
    if (draggedStick.value !== 'right') {
      rightStickX.value = applyDeadZone(gamepad.axes[2] ?? 0)
    }
  } else {
    gamepadName.value = ''
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
  animationFrame = requestAnimationFrame(updateGamepad)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
})
</script>

<template>
  <section class="joystick-section">
    <div class="joysticks-grid">
      <div class="joystick-column">
        <Joystick
          v-model="leftStickY"
          axis="vertical"
          label="Left joystick"
          @drag-start="draggedStick = 'left'"
          @drag-end="draggedStick = null"
        />
        <p class="joystick-readout">
          Y-velocity:
          <strong>{{ formatVelocity(yVelocity) }}</strong>
        </p>
      </div>

      <div class="joystick-center">
        <div v-if="!gamepadName" class="controller-empty bg-light">
          <span class="controller-empty-icon">
            <Gamepad2 :size="30" aria-hidden="true" />
          </span>
          <h4>Controller tidak terdeteksi.</h4>
          <p>Tekan sembarang tombol pada controller untuk menghubungkan.</p>
        </div>
      </div>

      <div class="joystick-column">
        <Joystick
          v-model="rightStickX"
          axis="horizontal"
          label="Right joystick"
          @drag-start="draggedStick = 'right'"
          @drag-end="draggedStick = null"
        />
        <p class="joystick-readout">
          Theta-velocity:
          <strong>{{ formatVelocity(thetaVelocity) }}</strong>
        </p>
      </div>
    </div>
  </section>
</template>
