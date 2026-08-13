import { ref, onUnmounted } from 'vue'

export interface LoadResource {
  url: string
  type: 'image' | 'audio'
  weight: number
}

const BACKGROUND_PATHS = [
  '/resource/images/bg1.jpg',
  '/resource/images/bg2.jpg',
  '/resource/images/bg3.jpg',
  '/resource/images/bg4.jpg'
]

const BGM_PATH = '/resource/audio/永恒宁静的藏书塔.mp3'

const PHASE_HINTS = [
  { maxProgress: 10, hint: '正在初始化...' },
  { maxProgress: 30, hint: '正在加载场景资源...' },
  { maxProgress: 55, hint: '加载环境纹理...' },
  { maxProgress: 75, hint: '构建画面...' },
  { maxProgress: 90, hint: '最终校验中...' },
  { maxProgress: 100, hint: '即将完成...' }
]

const MIN_LOAD_TIME = 3000

export function useLoading() {
  const progress = ref(0)
  const hint = ref('正在初始化...')
  const isComplete = ref(false)
  const bgmTriggered = ref(false)
  const selectedBgIndex = ref(0)

  let startTime = 0
  let actualProgress = 0
  let displayProgress = 0
  let rafId: number | null = null

  function getHint(p: number): string {
    for (const phase of PHASE_HINTS) {
      if (p <= phase.maxProgress) {
        return phase.hint
      }
    }
    return '完成！'
  }

  function loadImage(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve()
      img.onerror = () => reject(new Error(`Failed to load image: ${url}`))
      img.src = url
    })
  }

  function loadAudio(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const audio = new Audio()
      audio.preload = 'auto'
      audio.oncanplaythrough = () => resolve()
      audio.onerror = () => reject(new Error(`Failed to load audio: ${url}`))
      audio.src = url
    })
  }

  async function preloadResources(): Promise<void> {
    selectedBgIndex.value = Math.floor(Math.random() * BACKGROUND_PATHS.length)

    const resources: LoadResource[] = [
      ...BACKGROUND_PATHS.map((url, i) => ({
        url,
        type: 'image' as const,
        weight: i === selectedBgIndex.value ? 50 : 12
      })),
      { url: BGM_PATH, type: 'audio' as const, weight: 30 }
    ]

    const totalWeight = resources.reduce((sum, r) => sum + r.weight, 0)
    let loadedWeight = 0

    const loadPromises = resources.map(async (resource) => {
      try {
        if (resource.type === 'image') {
          await loadImage(resource.url)
        } else {
          await loadAudio(resource.url)
        }
      } catch (e) {
        console.warn('Resource load failed:', resource.url)
      }
      loadedWeight += resource.weight
      actualProgress = (loadedWeight / totalWeight) * 100
    })

    await Promise.all(loadPromises)
    actualProgress = 100
  }

  function animate() {
    const elapsed = Date.now() - startTime

    const targetProgress = Math.min(actualProgress, 100)

    const easeSpeed = 0.08
    displayProgress += (targetProgress - displayProgress) * easeSpeed

    if (displayProgress > 99.5) {
      displayProgress = 100
    }

    progress.value = Math.round(displayProgress * 100) / 100
    hint.value = getHint(progress.value)

    if (!bgmTriggered.value && progress.value >= 30) {
      bgmTriggered.value = true
    }

    if (displayProgress >= 100 && elapsed >= MIN_LOAD_TIME && !isComplete.value) {
      isComplete.value = true
      hint.value = '完成！'
      progress.value = 100
      return
    }

    rafId = requestAnimationFrame(animate)
  }

  function start() {
    progress.value = 0
    hint.value = '正在初始化...'
    isComplete.value = false
    bgmTriggered.value = false
    actualProgress = 0
    displayProgress = 0
    startTime = Date.now()

    preloadResources()
    rafId = requestAnimationFrame(animate)
  }

  function stop() {
    if (rafId) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
  }

  onUnmounted(() => {
    stop()
  })

  return {
    progress,
    hint,
    isComplete,
    bgmTriggered,
    selectedBgIndex,
    start,
    stop
  }
}
