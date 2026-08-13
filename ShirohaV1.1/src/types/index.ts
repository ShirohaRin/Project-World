export interface WeatherData {
  temperature: number
  weatherCode: number
  weatherDesc: string
  weatherIcon: string
  city: string
}

export interface FortuneResult {
  level: 'great' | 'good' | 'normal' | 'bad'
  levelText: string
  luckyColor: string
  luckyNumber: number
  direction: string
  yi: string[]
  ji: string[]
  quote: string
}

export interface MusicTrack {
  id: string
  title: string
  artist: string
  cover?: string
  url: string
}

export type CardType = 'dashboard' | 'artwork' | 'about' | 'fortune' | 'music' | 'game' | 'tools'

export interface CardInfo {
  id: CardType
  title: string
  icon: string
}
