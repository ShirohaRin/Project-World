<template>
  <Transition name="loading">
    <div v-if="!isHidden" class="loading-layer">
      <div class="loading-content">
        <div class="loading-title">原野 · Shiroha领域</div>
        <div class="loading-bar-container">
          <div class="loading-bar-bg">
            <div class="loading-bar-fill" :style="{ width: progress + '%' }"></div>
          </div>
          <div class="loading-percentage" :class="{ complete: isComplete }">
            {{ Math.round(progress) }}%
          </div>
        </div>
        <div class="loading-hint">{{ hint }}</div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useLoading } from '@/composables/useLoading'

interface Props {
  autoStart?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  autoStart: true
})

const emit = defineEmits<{
  (e: 'complete'): void
  (e: 'bgm-trigger'): void
}>()

const { progress, hint, isComplete, bgmTriggered, start } = useLoading()
const isHidden = ref(false)

watch(isComplete, (val) => {
  if (val) {
    emit('complete')
    setTimeout(() => {
      isHidden.value = true
    }, 1100)
  }
})

watch(bgmTriggered, (val) => {
  if (val) {
    emit('bgm-trigger')
  }
})

onMounted(() => {
  if (props.autoStart) {
    start()
  }
})
</script>

<style scoped>
.loading-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-index-loading);
  background: rgba(10, 15, 8, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-content {
  text-align: center;
  width: min(480px, 80vw);
}

.loading-title {
  font-size: clamp(1.5rem, 4vw, 2rem);
  font-weight: 600;
  color: var(--color-accent-gold);
  margin-bottom: 40px;
  text-shadow: 0 0 20px rgba(240, 208, 120, 0.5);
  letter-spacing: 0.1em;
}

.loading-bar-container {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.loading-bar-bg {
  flex: 1;
  height: 4px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  overflow: hidden;
}

.loading-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-accent-gold), #fff5cc);
  border-radius: 2px;
  transition: width 0.1s linear;
  box-shadow: 0 0 10px var(--color-accent-gold);
}

.loading-percentage {
  font-family: var(--font-family-mono);
  font-size: 0.9rem;
  color: var(--color-text-secondary);
  min-width: 48px;
  text-align: right;
  transition: all 0.3s ease;
}

.loading-percentage.complete {
  color: var(--color-accent-gold);
  animation: pulse-complete 0.8s ease-in-out infinite;
}

.loading-hint {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  text-align: left;
  opacity: 0.8;
}

.loading-enter-active {
  transition: opacity 0.5s ease;
}

.loading-leave-active {
  transition: all 0.8s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.loading-leave-to {
  transform: translateY(-100%);
  opacity: 0;
}
</style>
