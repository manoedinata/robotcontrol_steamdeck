import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import SettingsView from '../views/SettingsView.vue'

const routes = [
    {
        path: '/',
        name: 'home',
        component: HomeView,
        meta: { title: 'Home' },
    },
    {
        path: '/settings',
        name: 'settings',
        component: SettingsView,
        meta: { title: 'Settings' },
    },
]

// Hash history works reliably when the app is served from a file:// URL
// inside Electron, where there is no server to handle deep links.
const router = createRouter({
    history: createWebHashHistory(),
    routes,
})

export default router
