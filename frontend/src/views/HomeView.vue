<script setup>
import { computed, onBeforeUnmount, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Battery, BatteryCharging, Camera, Gamepad2, LoaderCircle, Server } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'
import { useDeckBattery } from '../composables/useDeckBattery'

const { cameraUrl, cameraSources, activeCameraIndex, cameraFeeds, switchCamera } = useSettings()
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
  : telemetry.value?.battery_level))
const batteryLabel = computed(() => batteryLevel.value === undefined
  ? '--'
  : `${batteryLevel.value}%`)
// The two sources report health differently -- the robot's telemetry can go
// stale, the Deck's read can be unavailable -- so each keeps its own wording.
const batteryState = computed(() => (showingDeckBattery.value
  ? deckBatteryState.value
  : telemetryState.value))
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
    stale: `Robot battery ${batteryLabel.value}, telemetry stale`,
    waiting: 'Waiting for robot telemetry',
  }[telemetryState.value]))
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
    <CameraFeed v-for="feed in cameraFeeds" v-show="feed.index === activeCameraIndex" :key="feed.key"
      :stream-id="feed.streamId" @status-change="(state) => onFeedStatus(feed.id, state)" />

    <header class="hud-brand" aria-label="Application title">
      <span>
        <strong>Robot Control</strong>
        <small>Steam Deck</small>
      </span>
    </header>

    <div class="telemetry-bar" aria-label="Device telemetry">
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

      <div class="telemetry-divider" aria-hidden="true"></div>

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
