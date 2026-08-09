import { readFile } from 'node:fs/promises'

const root = new URL('../', import.meta.url)
const source = await readFile(new URL('electron/main.ts', root), 'utf8')
const repair = await readFile(new URL('scripts/repair-srh-session-migration.mjs', root), 'utf8')

const requiredPatterns = [
  /const legacyUserDataPath = app\.getPath\('userData'\)/,
  /const fixedUserDataPath = join\(app\.getPath\('appData'\), isOwnerClient \? 'ProjectIDEA-SRH' : 'idea-assistant'\)/,
  /app\.setPath\('userData', fixedUserDataPath\)/,
  /async function migrateLegacyOwnerSession\(\): Promise<void>/,
  /if \(!isOwnerClient\) return/,
  /const legacyLocalStatePath = join\(legacyUserDataPath, 'Local State'\)/,
  /const markerPath = join\(app\.getPath\('userData'\), 'session-migration-v1'\)/,
  /await copyFile\(legacyLocalStatePath, targetLocalStatePath\)/,
  /await copyFile\(legacyPath, targetPath\)/,
  /await writeFile\(markerPath, 'migrated\\n', \{ flag: 'wx' \}\)/,
]

for (const pattern of requiredPatterns) {
  if (!pattern.test(source)) throw new Error(`Missing required session storage rule: ${pattern}`)
}

const migrationStart = source.indexOf('async function migrateLegacyOwnerSession')
const migrationEnd = source.indexOf('\n}\n\nfunction createDeviceId', migrationStart)
const migration = source.slice(migrationStart, migrationEnd)
if (migration.includes('unlink') || migration.includes('rm(')) throw new Error('Migration must not delete legacy files')
if (!migration.includes('if (!isOwnerClient) return')) throw new Error('Migration must be Owner-only')
if (!migration.includes('existsSync(markerPath)')) throw new Error('Migration must preserve post-migration SRH state')
if (!migration.includes('!existsSync(targetLocalStatePath)')) throw new Error('Migration must not overwrite existing Local State')
if (migration.indexOf('await copyFile(legacyLocalStatePath, targetLocalStatePath)') > migration.indexOf('await copyFile(legacyPath, targetPath)')) throw new Error('Local State must migrate before service config')
if (migration.includes('copyFile(legacyPath, targetPath)') && !migration.includes('existsSync(targetPath)')) throw new Error('Migration must not overwrite existing service config')

const readyStart = source.indexOf('app.whenReady().then(async () => {')
const migrationCall = source.indexOf('await migrateLegacyOwnerSession()', readyStart)
const firstIpc = source.indexOf('ipcMain.handle(', readyStart)
if (migrationCall < 0 || firstIpc < 0 || migrationCall > firstIpc) throw new Error('Migration must run before IPC handlers are registered')

for (const pattern of [
  /process\.env\.IDEA_REPAIR_SRH_SESSION !== '1'/,
  /join\(appData, 'idea-assistant'\)/,
  /join\(appData, 'ProjectIDEA-SRH'\)/,
  /\.pre-repair-\$\{timestamp\}\.bak/,
  /await rename\(target, /,
  /await copyFile\(join\(sourceDir, file\), join\(targetDir, file\)\)/,
]) {
  if (!pattern.test(repair)) throw new Error(`Missing repair rule: ${pattern}`)
}
if (!repair.includes("['Local State', 'service-config.json']")) throw new Error('Repair must handle both session files')
if (repair.includes('readFile(') || repair.includes('console.log(raw')) throw new Error('Repair must not print token contents')
if (repair.includes('unlink') || repair.includes('rm(')) throw new Error('Repair must not delete source files')

console.log('Session storage rules verified.')
