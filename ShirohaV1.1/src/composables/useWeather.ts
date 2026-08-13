import { ref, onMounted } from 'vue'
import type { WeatherData } from '@/types'

const weatherIconMap: Record<number, string> = {
  0: '☀️', 1: '🌤️', 2: '⛅', 3: '☁️', 45: '🌫️', 48: '🌫️',
  51: '🌧️', 53: '🌧️', 55: '🌧️', 61: '🌧️', 63: '🌧️', 65: '🌧️',
  71: '❄️', 73: '❄️', 75: '❄️', 77: '❄️', 80: '🌦️', 81: '🌦️', 82: '🌦️',
  95: '⛈️', 96: '⛈️', 99: '⛈️'
}

const weatherDescMap: Record<number, string> = {
  0: '晴朗', 1: '大部晴朗', 2: '局部多云', 3: '多云',
  45: '雾', 48: '沉积雾',
  51: '小毛毛雨', 53: '毛毛雨', 55: '大毛毛雨',
  61: '小雨', 63: '中雨', 65: '大雨',
  71: '小雪', 73: '中雪', 75: '大雪',
  77: '雪粒', 80: '小阵雨', 81: '中阵雨', 82: '大阵雨',
  95: '雷暴', 96: '冰雹雷暴', 99: '强冰雹雷暴'
}

export function useWeather() {
  const weather = ref<WeatherData>({
    temperature: 0,
    weatherCode: 0,
    weatherDesc: '获取中...',
    weatherIcon: '🌡️',
    city: '定位中...'
  })
  const isLoading = ref(true)
  const hasError = ref(false)

  async function fetchWeather() {
    try {
      const ipRes = await fetch('https://ipapi.co/json/')
      if (!ipRes.ok) throw new Error('IP定位失败')
      const ipData = await ipRes.json()
      const { city, latitude, longitude } = ipData

      const weatherRes = await fetch(
        `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current_weather=true&timezone=auto`
      )
      if (!weatherRes.ok) throw new Error('天气数据获取失败')
      const weatherData = await weatherRes.json()
      const current = weatherData.current_weather
      const temp = Math.round(current.temperature)
      const code = current.weathercode

      weather.value = {
        temperature: temp,
        weatherCode: code,
        weatherDesc: weatherDescMap[code] || '未知',
        weatherIcon: weatherIconMap[code] || '🌡️',
        city: city || '未知城市'
      }
      hasError.value = false
    } catch (err) {
      console.warn('天气更新失败:', err)
      weather.value = {
        temperature: 0,
        weatherCode: 0,
        weatherDesc: '获取失败',
        weatherIcon: '🌡️',
        city: '未知位置'
      }
      hasError.value = true
    } finally {
      isLoading.value = false
    }
  }

  onMounted(() => {
    fetchWeather()
  })

  return {
    weather,
    isLoading,
    hasError,
    fetchWeather
  }
}
