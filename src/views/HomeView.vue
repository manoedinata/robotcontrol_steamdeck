<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { BatteryMedium, Gamepad2, LoaderCircle } from '@lucide/vue'
import CameraFeed from '../components/CameraFeed.vue'
import ControllerPanel from '../components/ControllerPanel.vue'
import { useGamepad } from '../composables/useGamepad'
import { useSettings } from '../composables/useSettings'

const { cameraUrl } = useSettings()
const { gamepadName } = useGamepad()
const cameraState = ref('idle')
const batteryLevel = ref(null)
let removeBatteryListener

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

const statusLabel = computed(() => {
  if (cameraState.value === 'connected') return 'Connected'
  if (cameraState.value === 'loading') return 'Reconnecting'
  return 'Disconnected'
})

onMounted(() => {
  removeBatteryListener = window.electronAPI?.onUdpBatteryPercentage?.((percentage) => {
    if (Number.isInteger(percentage) && percentage >= 0 && percentage <= 100) {
      batteryLevel.value = percentage
    }
  })
})

onBeforeUnmount(() => removeBatteryListener?.())
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
      <div class="connection-telemetry" :title="statusLabel">
        <LoaderCircle v-if="cameraState === 'loading'" class="connection-spinner" :size="14" aria-hidden="true" />
        <span v-else class="connection-dot" :class="cameraState" aria-hidden="true"></span>
        <span class="telemetry-ip">{{ deviceAddress }}</span>
        <span class="visually-hidden">Camera {{ statusLabel }}</span>
      </div>
      <div class="telemetry-divider" aria-hidden="true"></div>
      <div class="battery-telemetry" title="Robot battery level">
        <BatteryMedium :size="20" aria-hidden="true" />
        <span>{{ batteryLevel === null ? '--' : `${batteryLevel}%` }}</span>
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
