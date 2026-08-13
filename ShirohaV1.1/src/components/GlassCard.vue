<template>
  <div class="glass-card" :class="{ 'card-hover': hoverable }" @click="handleClick">
    <div class="card-content">
      <slot></slot>
    </div>
  </div>
</template>

<script setup lang="ts">
interface Props {
  hoverable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  hoverable: true
})

const emit = defineEmits<{
  (e: 'click'): void
}>()

function handleClick() {
  if (props.hoverable) {
    emit('click')
  }
}
</script>

<style scoped>
.glass-card {
  background: var(--color-glass-bg);
  backdrop-filter: blur(var(--blur-glass));
  -webkit-backdrop-filter: blur(var(--blur-glass));
  border: 1px solid var(--color-glass-border);
  border-radius: var(--radius-card);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
  transition: all var(--transition-normal);
  overflow: hidden;
}

.card-hover {
  cursor: pointer;
}

.card-hover:hover {
  transform: scale(1.02);
  border-color: var(--color-glow-blue);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45), 0 0 20px var(--color-glow-blue);
}

.card-content {
  width: 100%;
  height: 100%;
}
</style>
