<template>
  <div id="app">
    <BackgroundLayer :loaded="isBgLoaded" />
    <DustParticles />

    <LoadingScreen
      :auto-start="true"
      @complete="onLoadingComplete"
      @bgm-trigger="onBgmTrigger"
    />

    <WelcomeScreen
      :visible="showWelcome"
      @complete="onWelcomeComplete"
    />

    <MainDashboard
      :visible="showMain"
      :username="username"
      @card-click="handleCardClick"
    />

    <BgmToggle v-if="showMain" />

    <FullscreenModal :visible="showModal" @close="showModal = false">
      <template v-if="currentCard === 'about'">
        <h2 class="modal-title">🧑‍💻 关于本人</h2>
        <div class="modal-content">
          <p>你好，我是 Shiroha~</p>
          <p>欢迎来到我的小站 🌸</p>
          <p>这里正在建设中，敬请期待...</p>
        </div>
      </template>
      <template v-else>
        <h2 class="modal-title">{{ cardTitles[currentCard as keyof typeof cardTitles] || '详情' }}</h2>
        <div class="modal-content">
          <p>该功能正在开发中...</p>
          <p class="placeholder-text">🚧 敬请期待 🚧</p>
        </div>
      </template>
    </FullscreenModal>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import BackgroundLayer from '@/components/BackgroundLayer.vue'
import DustParticles from '@/components/DustParticles.vue'
import LoadingScreen from '@/components/LoadingScreen.vue'
import WelcomeScreen from '@/components/WelcomeScreen.vue'
import MainDashboard from '@/components/MainDashboard.vue'
import BgmToggle from '@/components/BgmToggle.vue'
import FullscreenModal from '@/components/FullscreenModal.vue'
import { useBgm } from '@/composables/useBgm'

const { play } = useBgm()

const showWelcome = ref(false)
const showMain = ref(false)
const username = ref('访客')
const showModal = ref(false)
const currentCard = ref('')
const isBgLoaded = ref(false)

const cardTitles = {
  dashboard: '📅 今日概览',
  artwork: '🎨 艺术作品',
  about: '🧑‍💻 关于本人',
  fortune: '🔮 今日运势',
  music: '🎵 此刻音乐',
  game: '🕹️ 小游戏',
  tools: '🔧 工具栏'
}

function onLoadingComplete() {
  isBgLoaded.value = true
  setTimeout(() => {
    showWelcome.value = true
  }, 200)
}

function onBgmTrigger() {
  play()
}

function onWelcomeComplete(name: string) {
  username.value = name
  showWelcome.value = false
  setTimeout(() => {
    showMain.value = true
  }, 300)
}

function handleCardClick(cardType: string) {
  currentCard.value = cardType
  showModal.value = true
}
</script>

<style>
.modal-title {
  font-size: 1.8rem;
  color: var(--color-accent-gold);
  margin-bottom: 24px;
}

.modal-content {
  line-height: 2;
  color: var(--color-text-primary);
}

.modal-content p {
  margin-bottom: 12px;
}

.placeholder-text {
  text-align: center;
  font-size: 1.5rem;
  margin-top: 40px !important;
  opacity: 0.6;
}
</style>
