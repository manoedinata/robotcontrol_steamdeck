<script setup>
import { Clapperboard, X } from '@lucide/vue'
import RecordingsView from '../views/RecordingsView.vue'

const emit = defineEmits(['close'])
</script>

<template>
    <div class="recordings-shell-backdrop" role="presentation" @pointerdown.self="emit('close')">
        <section class="recordings-shell" role="dialog" aria-modal="true"
            aria-labelledby="recordings-shell-title">
            <header class="recordings-shell-header">
                <h1 id="recordings-shell-title">
                    <Clapperboard :size="20" aria-hidden="true" />
                    Recordings
                </h1>
                <button class="drawer-close-button" type="button" title="Close recordings"
                    aria-label="Close recordings" @click="emit('close')">
                    <X :size="22" aria-hidden="true" />
                </button>
            </header>
            <RecordingsView @close="emit('close')" />
        </section>
    </div>
</template>
