<script setup>
import { computed, ref } from 'vue'

// A single-axis joystick puck. `axis` decides which direction the puck can
// travel; the position is exposed through v-model as a normalized -1..1 value.
// Drag start/end are emitted so a parent can pause hardware gamepad overrides
// while the pointer is in control.
//
// `min` and `max` bound that travel. They are in the puck's own screen-facing
// coordinates, where a vertical axis reads -1 at the top and +1 at the bottom,
// so a stick that may only be pushed up is given a max of 0. The half it
// cannot reach is dimmed rather than left looking available.
const props = defineProps({
  modelValue: { type: Number, default: 0 },
  axis: {
    type: String,
    default: 'vertical',
    validator: (value) => value === 'vertical' || value === 'horizontal',
  },
  label: { type: String, default: 'Joystick' },
  min: { type: Number, default: -1 },
  max: { type: Number, default: 1 },
})

const emit = defineEmits(['update:modelValue', 'dragStart', 'dragEnd'])

const dragging = ref(false)

// The side the puck cannot travel to, so the ring can show it as unavailable.
const blockedHalf = computed(() => {
  if (props.max <= 0 && props.min < 0) return props.axis === 'vertical' ? 'bottom' : 'right'
  if (props.min >= 0 && props.max > 0) return props.axis === 'vertical' ? 'top' : 'left'
  return null
})

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
  const offset = props.axis === 'horizontal'
    ? (event.clientX - (bounds.left + radius)) / radius
    : (event.clientY - (bounds.top + radius)) / radius

  // Clamped into the bounds, not ignored outside them: dragging past the end
  // of the travel should hold the puck at full travel, the way a real stick
  // does, rather than leaving it wherever it last was.
  return Math.max(props.min, Math.min(props.max, offset))
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
    <div class="joystick-stage" :aria-label="label" @pointerdown="startDrag" @pointermove="moveDrag"
      @pointerup="stopDrag" @pointercancel="stopDrag">
      <div class="joystick-ring" :class="blockedHalf && `joystick-ring--blocked-${blockedHalf}`">
        <span class="axis axis-horizontal"></span>
        <span class="axis axis-vertical"></span>
        <div class="joystick-puck" :style="puckStyle"></div>
      </div>
    </div>
  </div>
</template>
