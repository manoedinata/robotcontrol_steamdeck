<script setup>
import { computed, ref } from 'vue'

// A single-axis joystick puck. `axis` decides which direction the puck can
// travel; the position is exposed through v-model as a normalized -1..1 value.
// Drag start/end are emitted so a parent can pause hardware gamepad overrides
// while the pointer is in control.
const props = defineProps({
  modelValue: { type: Number, default: 0 },
  axis: {
    type: String,
    default: 'vertical',
    validator: (value) => value === 'vertical' || value === 'horizontal',
  },
  label: { type: String, default: 'Joystick' },
})

const emit = defineEmits(['update:modelValue', 'dragStart', 'dragEnd'])

const dragging = ref(false)

const puckStyle = computed(() => {
  const offset = props.modelValue
  if (props.axis === 'horizontal') {
    return {
      left: `calc(50% + ${offset * 50}% - ${offset * 10}px)`,
      transform: 'translate(-50%, -50%)',
    }
  }
  return {
    top: `calc(50% + ${offset * 50}% - ${offset * 10}px)`,
    transform: 'translate(-50%, -50%)',
  }
})

function positionFromEvent(event) {
  const bounds = event.currentTarget.getBoundingClientRect()
  const radius = bounds.width / 2

  if (props.axis === 'horizontal') {
    return Math.max(-1, Math.min(1, (event.clientX - (bounds.left + radius)) / radius))
  }
  return Math.max(-1, Math.min(1, (event.clientY - (bounds.top + radius)) / radius))
}

function startDrag(event) {
  dragging.value = true
  event.currentTarget.setPointerCapture(event.pointerId)
  emit('dragStart')
  emit('update:modelValue', positionFromEvent(event))
}

function moveDrag(event) {
  if (!dragging.value) return
  emit('update:modelValue', positionFromEvent(event))
}

function stopDrag(event) {
  if (!dragging.value) return

  dragging.value = false
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId)
  }

  emit('update:modelValue', 0)
  emit('dragEnd')
}
</script>

<template>
  <div class="joystick-control">
    <div
      class="joystick-stage"
      :aria-label="label"
      @pointerdown="startDrag"
      @pointermove="moveDrag"
      @pointerup="stopDrag"
      @pointercancel="stopDrag"
    >
      <span class="axis axis-horizontal"></span>
      <span class="axis axis-vertical"></span>
      <div class="joystick-ring">
        <div class="joystick-puck" :style="puckStyle"></div>
      </div>
    </div>
  </div>
</template>
