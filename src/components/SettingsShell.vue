<script setup>
import { X } from '@lucide/vue'
import SettingsView from '../views/SettingsView.vue'

const emit = defineEmits(['close'])
</script>

<template>
    <div class="settings-shell-backdrop" role="presentation" @pointerdown.self="emit('close')">
        <aside class="settings-shell" role="dialog" aria-modal="true" aria-labelledby="settings-shell-title">
            <header class="settings-shell-header">
                <div>
                    <h1 id="settings-shell-title">Settings</h1>
                </div>
                <button class="drawer-close-button" type="button" title="Close settings" aria-label="Close settings"
                    @click="emit('close')">
                    <X :size="22" aria-hidden="true" />
                </button>
            </header>
            <SettingsView @close="emit('close')" />
        </aside>
    </div>
</template>