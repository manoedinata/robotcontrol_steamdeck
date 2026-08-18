<script setup>
import { computed, ref } from 'vue'
import { Battery, Camera, Gamepad2, LoaderCircle, Server } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'

const { cameraUrl } = useSettings()
const { gamepadName } = useGamepad()
const { telemetry, telemetryState, pingMs, pingState } = useBackendConnection()
const cameraState = ref('idle')

const gamepadStatusLabel = computed(() => gamepadName.value
  ? 'Connected'
  : 'Disconnected. Press any button to activate')

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
    <CameraFeed @status-change="cameraState = $event" />

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
          <span class="visually-hidden">Camera {{ statusLabel }}</span>
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
