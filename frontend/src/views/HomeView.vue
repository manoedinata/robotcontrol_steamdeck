<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Battery, BatteryCharging, Camera, Gamepad2, LoaderCircle, Route, Server } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'
import { useDeckBattery } from '../composables/useDeckBattery'
import { useOdometry } from '../composables/useOdometry'

const {
  cameraUrl,
  cameraSources,
  activeCameraIndex,
  cameraFeeds,
  switchCamera,
  ptzControlsActiveCamera,
  ptzSpeedMultiplier,
  ptzSpeedMultiplierMax,
  setPtzSpeedMultiplier,
  savePtzSpeedMultiplier,
} = useSettings()
const {
  travelled: distanceMetres,
  headingDegrees,
  x: poseX,
  y: poseY,
  countsLeft,
  countsRight,
  reset: resetOdometry,
} = useOdometry()
const { gamepadName, registerHandler } = useGamepad()
const { telemetry, telemetryState, pingMs, pingState, recordingState, recordingStale } = useBackendConnection()
const { deckBatteryLevel, deckBatteryCharging, deckBatteryState } = useDeckBattery()

// Every configured source is mounted and connected at once; this tracks each
// feed's status by id so the HUD can reflect just the visible one.
const cameraStates = reactive({})
function onFeedStatus(id, state) {
  cameraStates[id] = state
}
const cameraState = computed(() => {
  const active = cameraFeeds.value[activeCameraIndex.value]
  return (active && cameraStates[active.id]) || 'idle'
})
// Recording HUD. The backend owns the state; this only renders it, including
// while the socket is briefly down and ffmpeg is still writing.
const isRecording = computed(() => recordingState.value?.active === true)
const now = ref(Date.now())
let elapsedTimer = null

const failedSources = computed(() =>
  (recordingState.value?.sources ?? []).filter((source) => source.status === 'failed'))

const recordingElapsed = computed(() => {
  const startedAt = recordingState.value?.started_at
  if (!startedAt) return '0:00'
  const seconds = Math.max(Math.floor(now.value / 1000 - startedAt), 0)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
})

const recordingLabel = computed(() => {
  if (!isRecording.value) return 'Not recording'
  const failed = failedSources.value.length
  const total = recordingState.value?.sources?.length ?? 0
  const scope = failed
    ? `${total - failed} of ${total} sources recording, ${failed} failed`
    : `${total} source${total === 1 ? '' : 's'} recording`
  return `Recording for ${recordingElapsed.value}. ${scope}.`
})

onMounted(() => {
  elapsedTimer = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (elapsedTimer !== null) clearInterval(elapsedTimer)
  if (resetHoldTimer !== null) clearTimeout(resetHoldTimer)
  if (resetFlashTimer !== null) clearTimeout(resetFlashTimer)
})

let unregisterGamepadHandler

// Circle cycles the live camera source. Priority above the action bar so the
// switch works wherever focus sits on the home screen, but modal dialogs
// (settings, on-screen keyboard) keep their own cancel handling.
function handleGamepadAction(action) {
  if (action !== 'cancel' || cameraSources.value.length < 2) return false
  if (document.querySelector('[role="dialog"][aria-modal="true"]')) return false
  switchCamera(1)
  return true
}

onMounted(() => {
  unregisterGamepadHandler = registerHandler(handleGamepadAction, 20)
})

onBeforeUnmount(() => unregisterGamepadHandler?.())

const gamepadStatusLabel = computed(() => gamepadName.value
  ? 'Connected'
  : 'Disconnected. Press any button to activate')

const cameraLabel = computed(() => cameraSources.value.length > 1
  ? `Camera ${activeCameraIndex.value + 1}/${cameraSources.value.length}`
  : 'Camera')

const deviceAddress = computed(() => {
  try {
    return cameraUrl.value ? new URL(cameraUrl.value).hostname : '--'
  } catch {
    return '--'
  }
})

// How far the robot has driven, integrated from the two wheel encoders by
// `useOdometry`. It is signed: reversing winds it back, so it reads travel made
// good rather than an odometer that only ever climbs.
const hasOdometry = computed(() => telemetry.value?.position_left !== undefined
  && telemetry.value?.position_left !== null)

// Metres up to a kilometre, then kilometres: a drive is read at a glance, and
// a four-digit metre count is neither quick to read nor worth its width here.
// The thresholds go on the magnitude, since the value can be negative.
const distanceLabel = computed(() => {
  if (!hasOdometry.value) return '--'
  const metres = distanceMetres.value
  const size = Math.abs(metres)
  if (size >= 1000) return `${(metres / 1000).toFixed(2)} km`
  if (size >= 100) return `${Math.round(metres)} m`
  return `${metres.toFixed(1)} m`
})

// Debug readout: the two encoder frames, counted from where this run started,
// before the scale and the integration. Measured from the run's start rather
// than shown absolute so the reset zeroes them too, which is what makes them
// comparable with the distance beside them -- the mean of the two, times the
// metres per count, is what that distance should read. The absolute counts the
// robot sent are in the strip's tooltip, for when those are the question.
function countLabel(value) {
  return typeof value === 'number' ? value.toFixed(1) : '--'
}

const encoderLeftLabel = computed(() => countLabel(countsLeft.value))
const encoderRightLabel = computed(() => countLabel(countsRight.value))

const encoderDebugTitle = computed(() => 'Pose, and the encoder counts since the'
  + ' distance was last reset. The robot reports '
  + `${countLabel(telemetry.value?.position_left)} left, `
  + `${countLabel(telemetry.value?.position_right)} right.`)

// Pose, beside the counts it was integrated from: metres east and north of
// wherever the odometry was last zeroed, and the heading it has turned through.
const poseXLabel = computed(() => (hasOdometry.value ? poseX.value.toFixed(2) : '--'))
const poseYLabel = computed(() => (hasOdometry.value ? poseY.value.toFixed(2) : '--'))
const poseThetaLabel = computed(() => (hasOdometry.value
  ? `${headingDegrees.value.toFixed(1)}°`
  : '--'))

// Holding the distance zeroes it, along with the pose and the baseline the
// encoder counts are differenced against. A hold rather than a tap: this sits
// beside a live camera feed on a touchscreen the operator is holding, and a
// stray thumb must not throw away a drive's worth of odometry.
const RESET_HOLD_MS = 700
const resetHolding = ref(false)
const resetJustDone = ref(false)
let resetHoldTimer = null
let resetFlashTimer = null

function endResetHold() {
  if (resetHoldTimer !== null) {
    clearTimeout(resetHoldTimer)
    resetHoldTimer = null
  }
  resetHolding.value = false
}

function beginResetHold() {
  endResetHold()
  resetHolding.value = true
  resetHoldTimer = setTimeout(() => {
    resetHoldTimer = null
    resetHolding.value = false
    resetOdometry()
    // Say it happened: zeroes alone read the same as a robot that has not
    // moved yet, and the operator needs to know the hold was long enough.
    resetJustDone.value = true
    if (resetFlashTimer !== null) clearTimeout(resetFlashTimer)
    resetFlashTimer = setTimeout(() => {
      resetFlashTimer = null
      resetJustDone.value = false
    }, 1200)
  }, RESET_HOLD_MS)
}

// With no slider beside it the distance would stretch the full width of the
// telemetry above it. It keeps to the same right-hand column it occupies when
// the slider is there, so the two layouts read alike.
const distanceBarStyle = computed(() => {
  if (ptzControlsActiveCamera.value) return null
  return speedBarWidth.value
    ? { marginLeft: `calc(${speedBarWidth.value} + 6px)` }
    : { marginLeft: '50%' }
})

const distanceStatusLabel = computed(() => {
  if (resetJustDone.value) return 'Distance and pose reset'
  if (!hasOdometry.value) return 'Waiting for the robot\'s wheel positions. Hold to reset.'
  return `Travelled ${distanceLabel.value}, heading ${headingDegrees.value.toFixed(0)} degrees.`
    + ' Hold to reset the distance and pose.'
})

// Pan/tilt speed, as a multiple of the camera's slowest step. Dragging applies
// it live -- the operator is watching the camera, not the slider -- and letting
// go is what writes it to disk.
const speedLabel = computed(() => `Pan and tilt speed ${ptzSpeedMultiplier.value}x`
  + ` of ${ptzSpeedMultiplierMax}x`)

function onSpeedInput(event) {
  setPtzSpeedMultiplier(event.target.value)
}

function onSpeedChange(event) {
  savePtzSpeedMultiplier(event.target.value)
}

// The slider belongs to the camera, not to the ping beside it, so it ends
// where that half of the bar does. Where the divider sits depends on the
// address and on whether the camera count is shown, so it is measured rather
// than guessed at.
const telemetryBar = ref(null)
const telemetryDivider = ref(null)
const speedBarWidth = ref(null)
let barObserver = null

function measureSpeedBar() {
  const bar = telemetryBar.value
  const divider = telemetryDivider.value
  if (!bar || !divider) return
  // Up to and including the divider line itself, so the two right edges meet.
  const width = divider.getBoundingClientRect().right - bar.getBoundingClientRect().left
  speedBarWidth.value = width > 0 ? `${Math.round(width)}px` : null
}

onMounted(() => {
  measureSpeedBar()
  barObserver = new ResizeObserver(measureSpeedBar)
  barObserver.observe(telemetryBar.value)
})

onBeforeUnmount(() => {
  barObserver?.disconnect()
  barObserver = null
})

// The bar is the same width whether or not the slider is under it, so showing
// the slider does not itself resize what it is measured against; this covers
// the first paint after it appears.
watch(ptzControlsActiveCamera, async (controls) => {
  if (controls) {
    await nextTick()
    measureSpeedBar()
  }
})

const pingLabel = computed(() => pingMs.value === null ? '--' : `${Math.round(pingMs.value)} ms`)
const pingStatusLabel = computed(() => ({
  live: `UDP ping ${pingLabel.value}`,
  unavailable: 'UDP ping unavailable',
  waiting: 'Waiting for UDP ping',
}[pingState.value]))

// One readout, two batteries: the robot's over telemetry and the Deck's own
// from sysfs. Tapping it swaps which is on show, because both matter on a
// drive and the HUD has room for one.
const batterySource = ref('robot')
const showingDeckBattery = computed(() => batterySource.value === 'deck')

function toggleBatterySource() {
  batterySource.value = showingDeckBattery.value ? 'robot' : 'deck'
}

const batteryLevel = computed(() => (showingDeckBattery.value
  ? deckBatteryLevel.value ?? undefined
  : telemetry.value?.battery_level ?? undefined))
const batteryLabel = computed(() => batteryLevel.value === undefined
  ? '--'
  : `${batteryLevel.value}%`)
// The two sources report health differently -- the robot's telemetry can go
// stale, the Deck's read can be unavailable -- so each keeps its own wording.
// The robot's telemetry packet carries wheel positions and speeds and no
// battery at all, so a live feed still has nothing to say about its charge.
// That is "not reported", not "stale": more packets will not help.
const batteryState = computed(() => {
  if (showingDeckBattery.value) return deckBatteryState.value
  if (telemetryState.value === 'live' && batteryLevel.value === undefined) return 'unavailable'
  return telemetryState.value
})
const batteryIcon = computed(() => (showingDeckBattery.value && deckBatteryCharging.value
  ? BatteryCharging
  : Battery))
const batterySourceLabel = computed(() => (showingDeckBattery.value ? 'Deck' : 'Robot'))
const batteryStatusLabel = computed(() => (showingDeckBattery.value
  ? {
    live: `Deck battery ${batteryLabel.value}${deckBatteryCharging.value ? ', charging' : ''}`,
    unavailable: 'Deck battery unavailable',
    waiting: 'Reading Deck battery',
  }[deckBatteryState.value]
  : {
    live: `Robot battery ${batteryLabel.value}`,
    unavailable: 'Robot battery not reported',
    stale: `Robot battery ${batteryLabel.value}, telemetry stale`,
    waiting: 'Waiting for robot telemetry',
  }[batteryState.value]))
const batteryToggleLabel = computed(() => `${batteryStatusLabel.value}. `
  + `Show the ${showingDeckBattery.value ? 'robot' : 'Deck'} battery instead.`)

const statusLabel = computed(() => {
  if (cameraState.value === 'connected') return 'Connected'
  if (cameraState.value === 'loading') return 'Reconnecting'
  return 'Disconnected'
})

</script>

<template>
  <div class="home-layout">
    <CameraFeed v-for="feed in cameraFeeds" v-show="feed.index === activeCameraIndex" :key="feed.id"
      :stream-id="feed.streamId" @status-change="(state) => onFeedStatus(feed.id, state)" />

    <header class="hud-brand" aria-label="Application title">
      <span>
        <strong>Robot Control</strong>
        <small>Steam Deck</small>
      </span>
    </header>

    <div class="telemetry-stack">
      <div ref="telemetryBar" class="telemetry-bar" aria-label="Device telemetry">
        <div class="camera-telemetry" :title="statusLabel">
          <div class="connection-telemetry">
            <Camera :size="20" aria-hidden="true" />
            <span class="telemetry-ip">{{ deviceAddress }}</span>
            <span v-if="cameraSources.length > 1" class="telemetry-value">{{ cameraLabel }}</span>
            <span class="visually-hidden">{{ cameraLabel }} {{ statusLabel }}</span>
            <LoaderCircle v-if="cameraState === 'loading'" class="connection-spinner" :size="14" aria-hidden="true" />
            <span v-else class="connection-dot" :class="cameraState" aria-hidden="true"></span>
          </div>
        </div>

        <div ref="telemetryDivider" class="telemetry-divider" aria-hidden="true"></div>

        <div class="udp-telemetry" :title="pingStatusLabel">
          <div class="connection-telemetry">
            <Server :size="20" aria-hidden="true" />
            <span class="telemetry-ip">Ping</span>
            <span class="telemetry-value ping-value">{{ pingLabel }}</span>
            <LoaderCircle v-if="pingState === 'waiting'" class="connection-spinner" :size="14" aria-hidden="true" />
            <span v-else class="connection-dot" :class="{ connected: pingState === 'live' }" aria-hidden="true"></span>
            <span class="visually-hidden">{{ pingStatusLabel }}</span>
          </div>
        </div>
      </div>

      <div class="telemetry-underbar">
        <div v-if="ptzControlsActiveCamera" class="ptz-speed-bar" :title="speedLabel"
          :style="speedBarWidth ? { width: speedBarWidth } : null">
          <input class="ptz-speed-slider" type="range" min="1" :max="ptzSpeedMultiplierMax"
            step="1" :value="ptzSpeedMultiplier" :aria-label="speedLabel"
            :aria-valuetext="`${ptzSpeedMultiplier}x`" @input="onSpeedInput"
            @change="onSpeedChange">
          <span class="ptz-speed-value" aria-hidden="true">{{ ptzSpeedMultiplier }}x</span>
        </div>

        <button class="distance-bar" :class="{ holding: resetHolding, reset: resetJustDone }"
          type="button" :style="distanceBarStyle" :title="distanceStatusLabel"
          :aria-label="distanceStatusLabel" @pointerdown.prevent="beginResetHold"
          @pointerup="endResetHold" @pointerleave="endResetHold" @pointercancel="endResetHold"
          @contextmenu.prevent>
          <Route :size="16" aria-hidden="true" />
          <span class="distance-value" aria-hidden="true">{{ distanceLabel }}</span>
          <span class="visually-hidden" role="status" aria-live="polite">{{ distanceStatusLabel }}</span>
        </button>
      </div>

      <div class="encoder-debug" :title="encoderDebugTitle" aria-hidden="true">
        <span>x</span>
        <span class="encoder-debug-value pose">{{ poseXLabel }}</span>
        <span>y</span>
        <span class="encoder-debug-value pose">{{ poseYLabel }}</span>
        <span>θ</span>
        <span class="encoder-debug-value pose heading">{{ poseThetaLabel }}</span>
        <span class="encoder-debug-divider"></span>
        <span>&Delta;L</span>
        <span class="encoder-debug-value">{{ encoderLeftLabel }}</span>
        <span>&Delta;R</span>
        <span class="encoder-debug-value">{{ encoderRightLabel }}</span>
      </div>
    </div>

    <div v-if="isRecording" class="recording-status" :class="{ stale: recordingStale }"
      :title="recordingLabel" role="status" aria-live="polite">
      <span class="recording-dot" aria-hidden="true"></span>
      <span class="recording-label" aria-hidden="true">REC {{ recordingElapsed }}</span>
      <span v-if="failedSources.length" class="recording-failed" aria-hidden="true">
        {{ failedSources.length }} failed
      </span>
      <span class="visually-hidden">{{ recordingLabel }}</span>
    </div>

    <button class="battery-telemetry" type="button" :title="batteryToggleLabel"
      :aria-label="batteryToggleLabel" @click="toggleBatterySource">
      <div class="connection-telemetry">
        <component :is="batteryIcon" :size="20" aria-hidden="true" />
        <span class="battery-source" aria-hidden="true">{{ batterySourceLabel }}</span>
        <span class="telemetry-value">{{ batteryLabel }}</span>
        <span class="connection-dot" :class="{ connected: batteryState === 'live' }" aria-hidden="true"></span>
      </div>
    </button>
    <span class="visually-hidden" role="status" aria-live="polite">{{ batteryStatusLabel }}</span>

    <div class="control-mode" :class="{ connected: gamepadName }"
      :title="gamepadName || 'No hardware controller detected'" role="status" aria-live="polite">
      <span class="gamepad-status-indicator" aria-hidden="true">
        <Gamepad2 :size="16" />
        <span class="gamepad-status-dot"></span>
      </span>
      <span class="gamepad-status-label">
        {{ gamepadStatusLabel }}
      </span>
    </div>

    <ControllerPanel />
  </div>
</template>
