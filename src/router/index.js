import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

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
        // Lazy-loaded so the settings view is split into its own chunk.
        component: () => import('../views/SettingsView.vue'),
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
