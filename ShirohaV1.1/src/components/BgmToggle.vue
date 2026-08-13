<template>
  <button
    class="bgm-toggle"
    :class="{ active: isPlaying }"
    @click="handleToggle"
    :title="isPlaying ? '暂停音乐 (M)' : '播放音乐 (M)'"
  >
    {{ isPlaying ? '🔊' : '🔇' }}
  </button>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useBgm } from '@/composables/useBgm'

const { isPlaying, toggle } = useBgm()

function handleToggle() {
  toggle()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key.toLowerCase() === 'm') {
    toggle()
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.bgm-toggle {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: var(--z-index-bgm);
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--color-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--color-glass-border);
  font-size: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.bgm-toggle:hover {
  transform: scale(1.1);
  border-color: var(--color-accent-gold);
}

.bgm-toggle.active {
  border-color: var(--color-accent-gold);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3), 0 0 15px rgba(240, 208, 120, 0.4);
}

@media (max-width: 640px) {
  .bgm-toggle {
    width: 40px;
    height: 40px;
    font-size: 16px;
    bottom: 16px;
    right: 16px;
  }
}
</style>
