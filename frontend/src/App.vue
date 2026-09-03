<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Crosshair, Focus, LogOut, Settings } from '@lucide/vue'
import { useGamepad } from './composables/useGamepad'
import { useBackendConnection } from './composables/useBackendConnection'
import { usePTZState } from './composables/usePTZState'
import HomeView from './views/HomeView.vue'
import SettingsShell from './components/SettingsShell.vue'

const { registerHandler } = useGamepad()
const { connect: connectBackend, disconnect: disconnectBackend } = useBackendConnection()
const { setFocus, setUiOwnsGamepad, enabled: ptzEnabled } = usePTZState()

// Focus buttons are hold-to-act: pointerdown starts the focus movement and
// pointerup/leave releases it, mirroring the backend deadman behavior so the
// camera stops focusing the moment the button is let go.
function focusPress(value) {
  setFocus(value)
}

function focusRelease() {
  setFocus(null)
}
const actionBar = ref(null)
const settingsOpen = ref(false)
const settingsButton = ref(null)
let unregisterGamepadHandler

function quitApp() {
  window.electronAPI?.quitApp()
}

async function openSettings() {
  settingsOpen.value = true
  await nextTick()
  document.querySelector('#settings-page [data-gamepad-control]')?.focus()
}

async function closeSettings() {
  settingsOpen.value = false
  await nextTick()
  settingsButton.value?.focus({ preventScroll: true })
}

// Settings navigates with the same D-Pad the camera uses, so hand the gamepad
// over to the drawer while it is open and stop the camera.
watch(settingsOpen, (open) => setUiOwnsGamepad(open), { immediate: true })

function handleGamepadNavigation(action) {
  if (settingsOpen.value || document.querySelector('[role="dialog"][aria-modal="true"]')) return false

  const items = [...(actionBar.value?.querySelectorAll('[data-shell-action]') ?? [])]
  const focusedIndex = items.indexOf(document.activeElement)

  if (action === 'up' || action === 'down' || action === 'left' || action === 'right') {
    const offset = action === 'up' || action === 'left' ? -1 : 1
    const currentIndex = focusedIndex >= 0 ? focusedIndex : 0
    items[Math.max(0, Math.min(items.length - 1, currentIndex + offset))]?.focus()
    return true
  }

  if (action === 'activate' && focusedIndex >= 0) {
    document.activeElement.click()
    return true
  }

  return focusedIndex >= 0
}

onMounted(() => {
  connectBackend()
  unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 10)
})

onBeforeUnmount(() => {
  unregisterGamepadHandler?.()
  disconnectBackend()
})
</script>

<template>
  <div class="app-shell">
    <main class="content-shell">
      <HomeView />
    </main>

    <nav v-if="ptzEnabled" class="shell-actions shell-actions--left" aria-label="Camera focus controls">
      <button class="floating-icon-button focus-trigger" type="button" title="Focus near"
        aria-label="Focus near" @pointerdown.prevent="focusPress('near')" @pointerup="focusRelease()"
        @pointerleave="focusRelease()" @pointercancel="focusRelease()" @contextmenu.prevent>
        <Focus :size="21" aria-hidden="true" />
      </button>
      <button class="floating-icon-button focus-trigger" type="button" title="Focus far"
        aria-label="Focus far" @pointerdown.prevent="focusPress('far')" @pointerup="focusRelease()"
        @pointerleave="focusRelease()" @pointercancel="focusRelease()" @contextmenu.prevent>
        <Crosshair :size="21" aria-hidden="true" />
      </button>
    </nav>

    <nav ref="actionBar" class="shell-actions" aria-label="Application actions">
      <button class="floating-icon-button exit-trigger" type="button" title="Exit application"
        aria-label="Exit application" data-shell-action @click="quitApp">
        <LogOut :size="21" aria-hidden="true" />
      </button>
      <button ref="settingsButton" class="floating-icon-button settings-trigger" type="button" title="Settings"
        aria-label="Open settings" data-shell-action @click="openSettings">
        <Settings :size="22" aria-hidden="true" />
      </button>
    </nav>

    <SettingsShell v-if="settingsOpen" @close="closeSettings" />
  </div>
</template>