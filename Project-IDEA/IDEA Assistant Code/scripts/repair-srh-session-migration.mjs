import { copyFile, mkdir, rename } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

if (process.env.IDEA_REPAIR_SRH_SESSION !== '1') {
  throw new Error('Set IDEA_REPAIR_SRH_SESSION=1 to run SRH session repair')
}

const appData = process.env.APPDATA
if (!appData) throw new Error('APPDATA is required')

const sourceDir = join(appData, 'idea-assistant')
const targetDir = join(appData, 'ProjectIDEA-SRH')
const files = ['Local State', 'service-config.json']
const timestamp = new Date().toISOString().replace(/[:.]/g, '-')

for (const file of files) {
  const source = join(sourceDir, file)
  if (!existsSync(source)) throw new Error(`Missing source file: ${source}`)
}

await mkdir(targetDir, { recursive: true })
for (const file of files) {
  const target = join(targetDir, file)
  if (existsSync(target)) await rename(target, `${target}.pre-repair-${timestamp}.bak`)
}
for (const file of files) {
  await copyFile(join(sourceDir, file), join(targetDir, file))
}

console.log('SRH session repair completed.')
