<script setup>
import { ref } from 'vue'
import { X } from '@lucide/vue'
import SettingsView from '../views/SettingsView.vue'

const emit = defineEmits(['close'])
const settingsView = ref(null)
const closing = ref(false)

async function requestClose() {
    if (closing.value) return
    closing.value = true

    const saved = await settingsView.value?.saveBeforeClose()
    if (saved) emit('close')
    else closing.value = false
}
</script>

<template>
    <div class="settings-shell-backdrop" role="presentation" @pointerdown.self="requestClose">
        <aside class="settings-shell" role="dialog" aria-modal="true" aria-labelledby="settings-shell-title">
            <header class="settings-shell-header">
                <div>
                    <h1 id="settings-shell-title">Settings</h1>
                </div>
                <button class="drawer-close-button" type="button" title="Close settings" aria-label="Close settings"
                    :disabled="closing" @click="requestClose">
                    <X :size="22" aria-hidden="true" />
                </button>
            </header>
            <SettingsView ref="settingsView" @close="requestClose" />
        </aside>
    </div>
</template>