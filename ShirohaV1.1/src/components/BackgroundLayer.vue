<template>
  <div class="background-layer">
    <div
      class="background-image"
      :class="{ 'image-loaded': loaded }"
      :style="{ backgroundImage: `url(${currentBg})` }"
    ></div>
    <div class="background-overlay"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface Props {
  loaded?: boolean
}

withDefaults(defineProps<Props>(), {
  loaded: false
})

const BACKGROUND_PATHS = [
  '/resource/images/bg1.jpg',
  '/resource/images/bg2.jpg',
  '/resource/images/bg3.jpg',
  '/resource/images/bg4.jpg'
]

const currentBg = ref('')

onMounted(() => {
  const randomIndex = Math.floor(Math.random() * BACKGROUND_PATHS.length)
  currentBg.value = BACKGROUND_PATHS[randomIndex]
})
</script>

<style scoped>
.background-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-index-bg);
  overflow: hidden;
}

.background-image {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  animation: ken-burns 28s ease-in-out infinite alternate;
  filter: blur(20px) brightness(0.7);
  transform: scale(1.1);
  transition: filter 1.5s ease, transform 1.5s ease;
}

.image-loaded {
  filter: blur(0) brightness(1);
  transform: scale(1);
}

.background-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    var(--color-overlay-start) 0%,
    var(--color-overlay-end) 100%
  );
  z-index: var(--z-index-overlay);
}
</style>
