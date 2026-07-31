<script setup>
import { Bot, House, LogOut, Settings } from '@lucide/vue'

const navigation = [
  { label: 'Home', icon: House, to: { name: 'home' } },
  { label: 'Settings', icon: Settings, to: { name: 'settings' } },
]

function quitApp() {
  window.electronAPI?.quitApp()
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar border-end" aria-label="Primary navigation">
      <RouterLink class="sidebar-brand" :to="{ name: 'home' }" aria-label="Robot Monitor home">
        <div class="brand-mark" aria-hidden="true">
          <Bot :size="22" />
        </div>
        <div>
          <span class="brand-title">Robot Control</span>
          <span class="brand-subtitle">Steam Deck</span>
        </div>
      </RouterLink>

      <nav class="sidebar-nav">
        <RouterLink
          v-for="item in navigation"
          :key="item.label"
          class="nav-link"
          active-class="active"
          :to="item.to"
        >
          <component :is="item.icon" :size="19" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <button class="nav-link exit-button" type="button" @click="quitApp">
        <LogOut :size="19" aria-hidden="true" />
        <span>Exit</span>
      </button>
    </aside>

    <main class="content-shell">
      <RouterView />
    </main>
  </div>
</template>