import { computed } from 'vue'
import type { FortuneResult } from '@/types'
import { djb2Hash, seededRandom, getTodayString } from '@/utils/hash'

const fortunes = [
  { level: 'great' as const, levelText: '大吉', quotes: ['今日运势爆棚，万事顺遂！', '好运连连，心想事成~'] },
  { level: 'good' as const, levelText: '吉', quotes: ['今日运气不错，加油哦！', '平稳顺利的一天~'] },
  { level: 'normal' as const, levelText: '平', quotes: ['平平淡淡才是真', '无功无过，平稳度日'] },
  { level: 'bad' as const, levelText: '凶', quotes: ['今日诸事不宜，小心为上', '运势低迷，低调行事~'] }
]

const luckyColors = ['#f0d078', '#c084fc', '#3b82f6', '#ef4444', '#10b981', '#f97316']
const directions = ['东', '南', '西', '北', '东南', '东北', '西南', '西北']
const yiOptions = [
  ['写代码', '喝咖啡', '听音乐', '散步'],
  ['学习', '阅读', '整理房间', '早睡'],
  ['约会', '看电影', '吃美食', '购物'],
  ['运动', '冥想', '写日记', '发呆']
]
const jiOptions = [
  ['熬夜', '吃垃圾食品', '拖延症'],
  ['冲动消费', '吵架', '摸鱼'],
  ['久坐不动', '喝太多咖啡', '熬夜刷手机']
]

export function useFortune(username: string) {
  const fortune = computed<FortuneResult>(() => {
    const todayStr = getTodayString()
    const seedStr = `${username}-${todayStr}`
    const hash = djb2Hash(seedStr)
    const rand = seededRandom(hash)

    const levelRand = rand()
    let levelIndex = 0
    if (levelRand < 0.15) levelIndex = 0
    else if (levelRand < 0.5) levelIndex = 1
    else if (levelRand < 0.85) levelIndex = 2
    else levelIndex = 3

    const levelInfo = fortunes[levelIndex]
    const luckyColor = luckyColors[Math.floor(rand() * luckyColors.length)]
    const luckyNumber = Math.floor(rand() * 9) + 1
    const direction = directions[Math.floor(rand() * directions.length)]
    const yiSet = yiOptions[Math.floor(rand() * yiOptions.length)]
    const jiSet = jiOptions[Math.floor(rand() * jiOptions.length)]
    const quote = levelInfo.quotes[Math.floor(rand() * levelInfo.quotes.length)]

    const yiCount = 2 + Math.floor(rand() * 2)
    const jiCount = 1 + Math.floor(rand() * 2)

    const shuffledYi = [...yiSet].sort(() => rand() - 0.5).slice(0, yiCount)
    const shuffledJi = [...jiSet].sort(() => rand() - 0.5).slice(0, jiCount)

    return {
      level: levelInfo.level,
      levelText: levelInfo.levelText,
      luckyColor,
      luckyNumber,
      direction,
      yi: shuffledYi,
      ji: shuffledJi,
      quote
    }
  })

  return { fortune }
}
