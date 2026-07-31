<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { House, LogOut, Settings } from '@lucide/vue'
import { useGamepad } from './composables/useGamepad'

const route = useRoute()
const router = useRouter()
const { registerHandler } = useGamepad()
const sidebar = ref(null)
let unregisterGamepadHandler

const navigation = [
  { label: 'Home', icon: House, to: { name: 'home' } },
  { label: 'Settings', icon: Settings, to: { name: 'settings' } },
]

function quitApp() {
  window.electronAPI?.quitApp()
}

function focusNavigation(routeName = route.name) {
  const item = sidebar.value?.querySelector(`[data-navigation-key="${routeName}"]`)
  item?.focus()
}

async function selectNavigation(offset) {
  const items = [...sidebar.value.querySelectorAll('[data-navigation-key]')]
  const focusedIndex = items.indexOf(document.activeElement)
  const routeIndex = items.findIndex((item) => item.dataset.navigationKey === route.name)
  const currentIndex = focusedIndex >= 0 ? focusedIndex : Math.max(0, routeIndex)
  const nextIndex = Math.max(0, Math.min(items.length - 1, currentIndex + offset))
  const nextItem = items[nextIndex]
  const nextRoute = nextItem.dataset.routeName

  if (nextRoute && nextRoute !== route.name) await router.push({ name: nextRoute })
  await nextTick()
  nextItem.focus()
}

function handleGamepadNavigation(action) {
  if (document.querySelector('[role="dialog"][aria-modal="true"]')) return false

  const activeElement = document.activeElement
  const sidebarActive = sidebar.value?.contains(activeElement)
  const settingsActive = Boolean(activeElement?.closest?.('#settings-page'))
  if (settingsActive) return false

  if (action === 'up' || action === 'down') {
    selectNavigation(action === 'up' ? -1 : 1)
    return true
  }

  if (action === 'activate' && sidebarActive) {
    const navigationKey = activeElement.dataset.navigationKey
    if (navigationKey === 'settings' && route.name === 'settings') {
      document.querySelector('#settings-page [data-gamepad-control]')?.focus()
    } else if (navigationKey === 'exit') {
      activeElement.click()
    }
    return true
  }

  return sidebarActive
}

onMounted(() => {
  unregisterGamepadHandler = registerHandler(handleGamepadNavigation, 10)
})

onBeforeUnmount(() => unregisterGamepadHandler?.())
</script>

<template>
  <div class="app-shell">
    <aside ref="sidebar" class="sidebar border-end" aria-label="Primary navigation">
      <RouterLink class="sidebar-brand" :to="{ name: 'home' }" aria-label="Robot Monitor home">

        <!-- in case mau pake logo -->
        <!-- <div class="brand-mark" aria-hidden="true">
          <Bot :size="22" />
        </div> -->

        <div>
          <span class="brand-title">Robot Control</span>
          <span class="brand-subtitle">Steam Deck</span>
        </div>
      </RouterLink>

      <nav class="sidebar-nav">
        <RouterLink v-for="item in navigation" :key="item.label" class="nav-link" active-class="active" :to="item.to"
          :data-navigation-key="item.to.name" :data-route-name="item.to.name">
          <component :is="item.icon" :size="19" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <button class="nav-link exit-button" type="button" data-navigation-key="exit" @click="quitApp">
        <LogOut :size="19" aria-hidden="true" />
        <span>Exit</span>
      </button>
    </aside>

    <main class="content-shell">
      <RouterView />
    </main>
  </div>
</template>