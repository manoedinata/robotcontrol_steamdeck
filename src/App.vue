<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { LogOut, Settings } from '@lucide/vue'
import { useGamepad } from './composables/useGamepad'
import HomeView from './views/HomeView.vue'
import SettingsShell from './components/SettingsShell.vue'

const { registerHandler } = useGamepad()
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
  unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 10)
})

onBeforeUnmount(() => unregisterGamepadHandler?.())
</script>

<template>
  <div class="app-shell">
    <main class="content-shell">
      <HomeView />
    </main>

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