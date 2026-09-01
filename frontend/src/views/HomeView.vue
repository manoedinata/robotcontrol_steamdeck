<script setup>
import { computed, onBeforeUnmount, onMounted, reactive } from 'vue'
import { Battery, Camera, Gamepad2, LoaderCircle, Server } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'

const { cameraUrl, cameraSources, activeCameraIndex, cameraFeeds, switchCamera } = useSettings()
const { gamepadName, registerHandler } = useGamepad()
const { telemetry, telemetryState, pingMs, pingState } = useBackendConnection()

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

const batteryLevel = computed(() => telemetry.value?.battery_level)
const batteryLabel = computed(() => batteryLevel.value === undefined
  ? '--'
  : `${batteryLevel.value}%`)
const telemetryStatusLabel = computed(() => ({
  live: `Battery ${batteryLabel.value}`,
  stale: `Battery ${batteryLabel.value}, telemetry stale`,
  waiting: 'Waiting for robot telemetry',
}[telemetryState.value]))

const statusLabel = computed(() => {
  if (cameraState.value === 'connected') return 'Connected'
  if (cameraState.value === 'loading') return 'Reconnecting'
  return 'Disconnected'
})

</script>

<template>
  <div class="home-layout">
    <CameraFeed v-for="feed in cameraFeeds" v-show="feed.index === activeCameraIndex" :key="feed.key"
      :mode="feed.type" :stream-id="feed.streamId" :ws-url="feed.wsUrl"
      @status-change="(state) => onFeedStatus(feed.id, state)" />

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

    <div class="battery-telemetry" :title="telemetryStatusLabel" role="status" aria-live="polite">
      <div class="connection-telemetry">
        <Battery :size="20" aria-hidden="true" />
        <span class="telemetry-value">{{ batteryLabel }}</span>
        <span class="connection-dot" :class="{ connected: telemetryState === 'live' }" aria-hidden="true"></span>
        <span class="visually-hidden">{{ telemetryStatusLabel }}</span>
      </div>
    </div>

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
