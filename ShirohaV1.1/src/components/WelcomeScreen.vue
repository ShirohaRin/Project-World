<template>
  <Transition name="welcome">
    <div v-if="visible" class="welcome-layer">
      <div class="welcome-content" :class="{ 'content-animate': animating }">
        <div class="welcome-greeting" :class="{ 'greeting-fade': animating }">
          欢迎来到Shiroha的小站
        </div>
        <div v-if="!animating" class="input-wrapper">
          <input
            ref="inputRef"
            v-model="username"
            type="text"
            class="username-input"
            placeholder="请输入你的名字..."
            maxlength="20"
            @keyup.enter="handleSubmit"
          />
          <button class="confirm-btn" @click="handleSubmit" :disabled="!username.trim()">
            进入
          </button>
        </div>

        <div v-if="showFinalText" class="final-greeting">
          <span>欢迎访问</span>
          <span class="username-highlight">{{ username }}</span>
        </div>

        <div v-if="showSpecialText" class="special-container">
          <span
            v-for="(char, idx) in specialChars"
            :key="idx"
            class="special-char"
            :class="{ 'char-visible': char.visible }"
            :style="char.style"
          >
            {{ char.text }}
          </span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'

interface Props {
  visible: boolean
}

interface SpecialChar {
  text: string
  visible: boolean
  style: Record<string, string>
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'complete', username: string): void
}>()

const specialColors: Record<string, string> = {
  'SRH': '#c084fc',
  'Shiroha Rin': '#c084fc',
  'Shiroha Nao': '#ffffff',
  'IDEA': '#3b82f6',
  'Hoshina': '#ef4444',
  'Shiroha Hoshina': '#ef4444'
}

const username = ref('')
const inputRef = ref<HTMLInputElement | null>(null)
const animating = ref(false)
const showFinalText = ref(false)
const showSpecialText = ref(false)
const specialChars = ref<SpecialChar[]>([])

let floatInterval: number | null = null

function isSpecialUser(name: string): string | null {
  return specialColors[name] || null
}

function handleSubmit() {
  const name = username.value.trim()
  if (!name || animating.value) return

  animating.value = true
  const specialColor = isSpecialUser(name)

  if (specialColor) {
    playSpecialAnimation(name, specialColor)
  } else {
    playDefaultAnimation(name)
  }
}

function playDefaultAnimation(name: string) {
  setTimeout(() => {
    showFinalText.value = true
    setTimeout(() => {
      emit('complete', name)
    }, 800)
  }, 1000)
}

function playSpecialAnimation(name: string, color: string) {
  const fullText = `欢迎回家，${name}`
  const chars: SpecialChar[] = []

  for (let i = 0; i < fullText.length; i++) {
    const isUsernamePart = i >= 5
    chars.push({
      text: fullText[i] === ' ' ? '\u00A0' : fullText[i],
      visible: false,
      style: {
        color: isUsernamePart ? color : '#ffffff',
        fontWeight: isUsernamePart ? '700' : '600',
        textShadow: isUsernamePart
          ? color === '#ffffff'
            ? '0 0 20px #ffffff, 0 0 45px #ffffff, 0 2px 6px rgba(0,0,0,0.5)'
            : `0 0 20px ${color}, 0 0 45px ${color}CC, 0 2px 6px rgba(0,0,0,0.5)`
          : '0 0 12px #ffffff, 0 0 25px rgba(255,255,255,0.8), 0 2px 4px rgba(0,0,0,0.4)'
      }
    })
  }

  setTimeout(() => {
    showSpecialText.value = true
    specialChars.value = chars

    nextTick(() => {
      chars.forEach((char, i) => {
        setTimeout(() => {
          char.visible = true
        }, i * 80)
      })

      const floatAmplitude = 4
      const floatSpeed = 2000
      const startTime = Date.now()

      floatInterval = window.setInterval(() => {
        const now = Date.now()
        const charEls = document.querySelectorAll('.special-char')
        charEls.forEach((el, idx) => {
          const phase = ((now - startTime) + idx * 200) % floatSpeed
          const progress = phase / floatSpeed
          const offset = Math.sin(progress * Math.PI * 2) * floatAmplitude
          ;(el as HTMLElement).style.transform = `translateY(${offset}px)`
        })
      }, 16)

      setTimeout(() => {
        if (floatInterval) {
          clearInterval(floatInterval)
        }
        emit('complete', name)
      }, 3000)
    })
  }, 1500)
}

onMounted(() => {
  setTimeout(() => {
    inputRef.value?.focus()
  }, 500)
})
</script>

<style scoped>
.welcome-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-index-interaction);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.welcome-content {
  text-align: center;
  pointer-events: auto;
  transition: all 0.8s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.welcome-greeting {
  font-size: clamp(1.8rem, 4vw, 2.5rem);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 32px;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
  letter-spacing: 0.05em;
  transition: all 1s ease;
}

.greeting-fade {
  opacity: 0;
  transform: scale(0.9);
  filter: blur(2px);
}

.input-wrapper {
  display: flex;
  gap: 12px;
  justify-content: center;
  animation: slide-up 0.8s ease-out 0.3s both;
}

.username-input {
  width: min(320px, 60vw);
  padding: 14px 20px;
  font-size: 1rem;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 12px;
  color: var(--color-text-primary);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  transition: all var(--transition-fast);
}

.username-input::placeholder {
  color: rgba(255, 255, 255, 0.4);
}

.username-input:focus {
  border-color: var(--color-accent-gold);
  box-shadow: 0 0 0 3px rgba(240, 208, 120, 0.2);
}

.confirm-btn {
  padding: 14px 28px;
  font-size: 1rem;
  font-weight: 600;
  background: var(--color-accent-gold);
  color: #1a1f14;
  border-radius: 12px;
  transition: all var(--transition-fast);
}

.confirm-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(240, 208, 120, 0.4);
}

.confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.final-greeting {
  font-size: clamp(1.2rem, 3vw, 1.8rem);
  color: var(--color-text-primary);
  animation: fade-in 0.5s ease both;
}

.username-highlight {
  color: var(--color-accent-gold);
  font-weight: 700;
  margin-left: 8px;
  text-shadow: 0 0 18px rgba(240, 208, 120, 0.7);
}

.special-container {
  font-size: clamp(1.8rem, 4vw, 2.8rem);
  display: flex;
  justify-content: center;
  animation: fade-in 0.5s ease both;
}

.special-char {
  display: inline-block;
  opacity: 0;
  transition: opacity 0.4s ease;
  white-space: pre;
}

.char-visible {
  opacity: 1;
}

.welcome-enter-active,
.welcome-leave-active {
  transition: all 0.6s ease;
}

.welcome-enter-from,
.welcome-leave-to {
  opacity: 0;
  transform: translateY(-20px);
}
</style>
