<script setup>
import { onMounted, ref } from 'vue'
import { Bot, House, LogOut, Settings } from '@lucide/vue'
import HomePage from './pages/HomePage.vue'
import SettingsPage from './pages/SettingsPage.vue'

const navigation = [
  { label: 'Home', icon: House },
  { label: 'Settings', icon: Settings },
]

const activeView = ref('Home')
const cameraUrl = ref('')

async function loadSettings() {
  try {
    const settings = await window.electronAPI?.loadSettings()
    cameraUrl.value = settings?.cameraUrl ?? ''
  } catch (error) {
    console.error(error)
  }
}

onMounted(loadSettings)

function quitApp() {
  window.electronAPI?.quitApp()
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar border-end" aria-label="Primary navigation">
      <a class="sidebar-brand" href="#" aria-label="Robot Monitor home">
        <div class="brand-mark" aria-hidden="true">
          <Bot :size="22" />
        </div>
        <div>
          <span class="brand-title">Robot Control</span>
          <span class="brand-subtitle">Steam Deck</span>
        </div>
      </a>

      <nav class="sidebar-nav">
        <button
          v-for="item in navigation"
          :key="item.label"
          class="nav-link"
          :class="{ active: activeView === item.label }"
          type="button"
          :aria-current="activeView === item.label ? 'page' : undefined"
          @click="activeView = item.label"
        >
          <component :is="item.icon" :size="19" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <button class="nav-link exit-button" type="button" @click="quitApp">
        <LogOut :size="19" aria-hidden="true" />
        <span>Exit</span>
      </button>
    </aside>

    <main class="content-shell" :aria-label="activeView">
      <HomePage v-if="activeView === 'Home'" :camera-url="cameraUrl" />
      <SettingsPage v-else :camera-url="cameraUrl" @saved="cameraUrl = $event" />
    </main>
  </div>
</template>