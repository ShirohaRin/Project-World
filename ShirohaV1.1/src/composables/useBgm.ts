import { ref, onMounted, onUnmounted } from 'vue'

const BGM_URL = '/resource/audio/永恒宁静的藏书塔.mp3'

export function useBgm() {
  const isPlaying = ref(false)
  const isEnabled = ref(false)
  const audio = ref<HTMLAudioElement | null>(null)

  function initAudio() {
    if (audio.value) return
    audio.value = new Audio(BGM_URL)
    audio.value.loop = true
    audio.value.volume = 0.4
  }

  async function play() {
    initAudio()
    if (!audio.value) return
    try {
      await audio.value.play()
      isPlaying.value = true
      isEnabled.value = true
    } catch (e) {
      isPlaying.value = false
    }
  }

  function pause() {
    if (audio.value) {
      audio.value.pause()
    }
    isPlaying.value = false
  }

  function stop() {
    if (audio.value) {
      audio.value.pause()
      audio.value.currentTime = 0
    }
    isPlaying.value = false
    isEnabled.value = false
  }

  function toggle() {
    if (isPlaying.value) {
      stop()
    } else {
      play()
    }
  }

  function handleFirstInteraction() {
    if (isEnabled.value && !isPlaying.value && audio.value) {
      play()
    }
  }

  onMounted(() => {
    document.addEventListener('click', handleFirstInteraction, { passive: true })
  })

  onUnmounted(() => {
    document.removeEventListener('click', handleFirstInteraction)
    if (audio.value) {
      audio.value.pause()
      audio.value = null
    }
  })

  return {
    isPlaying,
    isEnabled,
    play,
    pause,
    stop,
    toggle
  }
}
