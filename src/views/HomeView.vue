<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Camera, Unplug } from '@lucide/vue'
import { useSettings } from '../composables/useSettings'

const { cameraUrl } = useSettings()

const cameraSource = ref('')
const cameraState = ref('idle')
const cameraError = ref(null)
const gamepadName = ref('')
const leftStickY = ref(0)
const rightStickX = ref(0)
const draggedStick = ref(null)
let animationFrame

const leftStickStyle = computed(() => ({
  top: `calc(50% + ${leftStickY.value * 50}% - ${leftStickY.value * 10}px)`,
  transform: 'translate(-50%, -50%)',
}))

const rightStickStyle = computed(() => ({
  left: `calc(50% + ${rightStickX.value * 50}% - ${rightStickX.value * 10}px)`,
  transform: 'translate(-50%, -50%)',
}))

const yVelocity = computed(() => -leftStickY.value * 10)
const thetaVelocity = computed(() => rightStickX.value * 10)

function formatVelocity(value) {
  const rounded = Math.round(value * 10) / 10
  return Object.is(rounded, -0) ? '0.0' : rounded.toFixed(1)
}

function connectCamera(nextUrl) {
  const nextSource = nextUrl.trim()
  cameraError.value = null

  if (!nextSource) {
    cameraSource.value = ''
    cameraState.value = 'idle'
    return
  }

  cameraState.value = 'loading'
  cameraSource.value = nextSource
  console.info('[camera] Loading stream:', nextSource)
}

function handleCameraReady(event) {
  cameraState.value = 'connected'
  cameraError.value = null
  console.info('[camera] Stream ready', {
    currentSrc: event.currentTarget.currentSrc,
    width: event.currentTarget.naturalWidth,
    height: event.currentTarget.naturalHeight,
  })
}

function handleCameraError(event) {
  const image = event.currentTarget
  const detail = 'Image or MJPEG stream could not be loaded.'

  cameraState.value = 'error'
  cameraError.value = detail
  console.error('[camera] Stream failed', {
    requestedUrl: cameraUrl.value,
    currentSrc: image.currentSrc,
    complete: image.complete,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  })
}

function applyDeadZone(value, threshold = 0.12) {
  if (Math.abs(value) < threshold) return 0
  return Math.sign(value) * ((Math.abs(value) - threshold) / (1 - threshold))
}

function updateDraggedStick(event, stick) {
  if (draggedStick.value !== stick) return

  const bounds = event.currentTarget.getBoundingClientRect()
  const radius = bounds.width / 2
  const x = Math.max(-1, Math.min(1, (event.clientX - (bounds.left + radius)) / radius))
  const y = Math.max(-1, Math.min(1, (event.clientY - (bounds.top + radius)) / radius))

  stick === 'left' ? leftStickY.value = y : rightStickX.value = x
}

function startStickDrag(event, stick) {
  draggedStick.value = stick
  event.currentTarget.setPointerCapture(event.pointerId)
  updateDraggedStick(event, stick)
}

function stopStickDrag(event, stick) {
  if (draggedStick.value !== stick) return

  draggedStick.value = null
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId)
  }

  stick === 'left' ? leftStickY.value = 0 : rightStickX.value = 0
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

watch(cameraUrl, connectCamera, { immediate: true })

onMounted(() => {
  animationFrame = requestAnimationFrame(updateGamepad)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
})
</script>

<template>
  <div class="home-layout">
    <section class="camera-section">
      <div class="camera-viewport">
        <img
          v-if="cameraSource"
          :src="cameraSource"
          alt="Live IP camera feed"
          @load="handleCameraReady"
          @error="handleCameraError"
        />
        <div v-if="cameraState !== 'connected'" class="camera-message">
          <Camera v-if="cameraState !== 'error'" :size="34" aria-hidden="true" />
          <Unplug v-else :size="34" aria-hidden="true" />
          <strong v-if="cameraState === 'loading'">Connecting to camera...</strong>
          <strong v-else-if="cameraState === 'error'">Camera feed error</strong>
          <strong v-else>Camera not connected</strong>
          <span v-if="cameraState === 'error'">{{ cameraError }}</span>
          <span v-else-if="cameraState === 'idle'">Set the camera URL in Settings to start the feed.</span>
        </div>
        <span v-if="cameraState === 'connected'" class="live-indicator">Live</span>
      </div>
    </section>

    <section class="joystick-section">
      <div class="joysticks-grid">
        <div class="joystick-control">
          <div
            class="joystick-stage"
            aria-label="Left joystick"
            @pointerdown="startStickDrag($event, 'left')"
            @pointermove="updateDraggedStick($event, 'left')"
            @pointerup="stopStickDrag($event, 'left')"
            @pointercancel="stopStickDrag($event, 'left')"
          >
            <span class="axis axis-horizontal"></span>
            <span class="axis axis-vertical"></span>
            <div class="joystick-ring">
              <div class="joystick-puck" :style="leftStickStyle"></div>
            </div>
          </div>
        </div>

        <div class="joystick-center-card">
          <p>
            Joystick status:
            {{ gamepadName || 'Not connected' }}
          </p>
          <p>
            Y-velocity:
            <strong>{{ formatVelocity(yVelocity) }}</strong>
          </p>
          <p>
            Theta-velocity:
            <strong>{{ formatVelocity(thetaVelocity) }}</strong>
          </p>
        </div>

        <div class="joystick-control">
          <div
            class="joystick-stage"
            aria-label="Right joystick"
            @pointerdown="startStickDrag($event, 'right')"
            @pointermove="updateDraggedStick($event, 'right')"
            @pointerup="stopStickDrag($event, 'right')"
            @pointercancel="stopStickDrag($event, 'right')"
          >
            <span class="axis axis-horizontal"></span>
            <span class="axis axis-vertical"></span>
            <div class="joystick-ring">
              <div class="joystick-puck" :style="rightStickStyle"></div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
