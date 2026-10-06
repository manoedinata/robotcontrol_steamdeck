<script setup>
// Centre guides over the live feed: two perspective lines rising toward the
// middle of the frame and a small cross on the exact centre. The video fills
// the viewport with `object-fit: cover`, so the viewport's centre is the
// camera's centre. Drawn in a 0-100 box stretched to the screen; strokes keep
// their width through that stretch.
//
// Every line is drawn twice -- a wider dark one underneath, then the white one
// -- so the guides keep an outline against a bright scene.
const lines = [
  { x1: 32, y1: 100, x2: 46, y2: 58 },
  { x1: 68, y1: 100, x2: 54, y2: 58 },
  { x1: 48.5, y1: 50, x2: 51.5, y2: 50, centre: true },
  { x1: 50, y1: 47.5, x2: 50, y2: 52.5, centre: true },
]
</script>

<template>
  <svg class="camera-guides" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    <g class="camera-guides-outline">
      <line v-for="(line, index) in lines" :key="index" :class="{ 'camera-guides-centre': line.centre }"
        :x1="line.x1" :y1="line.y1" :x2="line.x2" :y2="line.y2" />
    </g>
    <g class="camera-guides-stroke">
      <line v-for="(line, index) in lines" :key="index" :class="{ 'camera-guides-centre': line.centre }"
        :x1="line.x1" :y1="line.y1" :x2="line.x2" :y2="line.y2" />
    </g>
  </svg>
</template>
