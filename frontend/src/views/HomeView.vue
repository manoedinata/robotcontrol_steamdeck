<script setup>
import { computed, ref } from 'vue'
import { Camera, Gamepad2, LoaderCircle, Server } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'
import { useBackendConnection } from '../composables/useBackendConnection'

const { cameraUrl } = useSettings()
const { gamepadName } = useGamepad()
const { connectionState } = useBackendConnection()
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

const backendStatusLabel = computed(() => ({
  connected: 'Connected',
  connecting: 'Connecting',
  disconnected: 'Disconnected',
}[connectionState.value] || 'Unavailable'))

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

      <div class="udp-telemetry" :title="backendStatusLabel">
        <div class="connection-telemetry">
          <Server :size="20" aria-hidden="true" />
          <span class="telemetry-ip">Backend</span>
          <LoaderCircle v-if="connectionState === 'connecting'" class="connection-spinner" :size="14"
            aria-hidden="true" />
          <span v-else class="connection-dot" :class="connectionState" aria-hidden="true"></span>
          <span class="visually-hidden">Backend: {{ backendStatusLabel }}</span>
        </div>
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
