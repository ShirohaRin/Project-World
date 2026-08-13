<template>
  <Transition name="main">
    <div v-if="visible" class="main-layer">
      <div class="main-container">
        <div class="left-column">
          <GlassCard class="dashboard-card" @click="$emit('card-click', 'dashboard')">
            <div class="card-header">
              <span class="card-title">📅 今日概览</span>
            </div>
            <div class="card-body">
              <div class="date-display" id="dateDisplay">{{ currentDate }}</div>
              <div class="weather-row">
                <span class="weather-icon">{{ weather.weatherIcon }}</span>
                <span class="temperature">{{ weather.temperature }}°C</span>
                <span class="weather-desc">{{ weather.weatherDesc }}</span>
              </div>
              <div class="activity-list">
                <p>📍 {{ weather.city }}</p>
                <p>🌸 最近更新：个人作品集 V2.3</p>
                <p>📝 新日志：《夏夜与代码》</p>
              </div>
            </div>
          </GlassCard>

          <div class="bottom-cards">
            <GlassCard class="bottom-card" @click="$emit('card-click', 'artwork')">
              <div class="card-icon">🎨</div>
              <div class="card-label">艺术作品</div>
            </GlassCard>
            <GlassCard class="bottom-card" @click="$emit('card-click', 'about')">
              <div class="card-icon">🧑‍💻</div>
              <div class="card-label">关于本人</div>
            </GlassCard>
          </div>
        </div>

        <div class="right-column">
          <GlassCard class="right-card fortune-card" @click="$emit('card-click', 'fortune')">
            <div class="card-icon">🔮</div>
            <div class="card-label">今日运势</div>
          </GlassCard>
          <GlassCard class="right-card music-card" @click="$emit('card-click', 'music')">
            <div class="card-icon">🎵</div>
            <div class="card-label">此刻音乐</div>
          </GlassCard>
          <div class="right-bottom-cards">
            <GlassCard class="small-card" @click="$emit('card-click', 'game')">
              <div class="card-icon">🕹️</div>
              <div class="card-label">小游戏</div>
            </GlassCard>
            <GlassCard class="small-card" @click="$emit('card-click', 'tools')">
              <div class="card-icon">🔧</div>
              <div class="card-label">工具栏</div>
            </GlassCard>
          </div>
        </div>
      </div>

      <div class="top-left-info">
        <span class="welcome-text">欢迎访问</span>
        <span class="user-name">{{ username }}</span>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import GlassCard from './GlassCard.vue'
import { useWeather } from '@/composables/useWeather'

interface Props {
  visible: boolean
  username: string
}

defineProps<Props>()

defineEmits<{
  (e: 'card-click', cardType: string): void
}>()

const { weather } = useWeather()

const currentDate = computed(() => {
  const now = new Date()
  const weekdays = ['日', '一', '二', '三', '四', '五', '六']
  return `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 星期${weekdays[now.getDay()]}`
})
</script>

<style scoped>
.main-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-index-main);
  padding: clamp(20px, 4vw, 60px);
  padding-top: clamp(60px, 10vh, 100px);
}

.main-container {
  display: flex;
  gap: clamp(16px, 2vw, 24px);
  height: 100%;
  max-width: 1400px;
  margin: 0 auto;
}

.left-column {
  flex: 1 1 65%;
  display: flex;
  flex-direction: column;
  gap: clamp(16px, 2vw, 24px);
}

.right-column {
  flex: 0 0 340px;
  display: flex;
  flex-direction: column;
  gap: clamp(12px, 1.5vw, 16px);
}

.dashboard-card {
  flex: 1;
  padding: 28px;
}

.card-header {
  margin-bottom: 20px;
}

.card-title {
  font-size: 1.3rem;
  font-weight: 600;
  color: var(--color-accent-gold);
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.date-display {
  font-size: 1.1rem;
  color: var(--color-text-secondary);
}

.weather-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 1.5rem;
}

.weather-icon {
  font-size: 2rem;
}

.temperature {
  font-family: var(--font-family-mono);
  font-weight: 700;
  color: var(--color-text-primary);
}

.weather-desc {
  font-size: 1rem;
  color: var(--color-text-secondary);
}

.activity-list {
  margin-top: 8px;
  line-height: 2;
  color: var(--color-text-secondary);
  font-size: 0.9rem;
}

.bottom-cards {
  display: flex;
  gap: clamp(12px, 1.5vw, 16px);
  height: 140px;
}

.bottom-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.right-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 120px;
}

.right-bottom-cards {
  display: flex;
  gap: 12px;
  height: 100px;
}

.small-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.card-icon {
  font-size: 2.5rem;
}

.card-label {
  font-size: 0.95rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.small-card .card-icon {
  font-size: 1.8rem;
}

.small-card .card-label {
  font-size: 0.85rem;
}

.top-left-info {
  position: fixed;
  top: clamp(24px, 4vh, 40px);
  left: clamp(24px, 5vw, 80px);
  z-index: 15;
  font-size: clamp(1rem, 2vw, 1.2rem);
}

.welcome-text {
  color: var(--color-text-primary);
}

.user-name {
  color: var(--color-accent-gold);
  font-weight: 700;
  margin-left: 8px;
  text-shadow: 0 0 18px rgba(240, 208, 120, 0.7);
}

.main-enter-active {
  transition: all 0.8s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.main-enter-from {
  opacity: 0;
  transform: translateY(30px);
}

@media (max-width: 768px) {
  .main-container {
    flex-direction: column;
  }

  .left-column,
  .right-column {
    flex: none;
    width: 100%;
  }

  .right-column {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .right-card {
    flex: 1 1 calc(50% - 8px);
    min-height: 100px;
  }

  .right-bottom-cards {
    width: 100%;
  }

  .dashboard-card {
    min-height: 200px;
  }

  .bottom-cards {
    height: 120px;
  }
}
</style>
