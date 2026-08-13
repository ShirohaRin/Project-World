<template>
  <div class="dust-particles">
    <div
      v-for="particle in particles"
      :key="particle.id"
      class="dust-particle"
      :style="particle.style"
    ></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface DustParticle {
  id: number
  style: Record<string, string>
}

const particles = ref<DustParticle[]>([])

onMounted(() => {
  const list: DustParticle[] = []
  for (let i = 0; i < 30; i++) {
    list.push({
      id: i,
      style: {
        '--x': `${Math.random() * 100}%`,
        '--dur': `${8 + Math.random() * 18}s`,
        '--delay': `${Math.random() * 20}s`,
        '--drift': `${(Math.random() - 0.5) * 120}px`,
        left: `var(--x)`,
        width: `${1 + Math.random() * 2.5}px`,
        height: `${1 + Math.random() * 2.5}px`,
        opacity: `${0.25 + Math.random() * 0.55}`,
        animationDuration: `var(--dur)`,
        animationDelay: `var(--delay)`
      }
    })
  }
  particles.value = list
})
</script>

<style scoped>
.dust-particles {
  position: fixed;
  inset: 0;
  z-index: var(--z-index-particles);
  pointer-events: none;
  overflow: hidden;
}

.dust-particle {
  position: absolute;
  bottom: -10px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 50%;
  animation: float-up linear infinite;
}
</style>
