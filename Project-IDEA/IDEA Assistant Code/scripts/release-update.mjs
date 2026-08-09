#!/usr/bin/env node
import { copyFile, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { basename, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
const args = new Map(process.argv.slice(2).map((value, index, values) => value.startsWith('--') ? [value.slice(2), values[index + 1]] : []))
const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const packagePath = join(root, 'package.json')
const prepareVersion = args.get('prepareVersion')

if (prepareVersion !== undefined) {
  if (!SEMVER.test(prepareVersion)) throw new Error('prepareVersion 必须是合法的 SemVer 版本号')
  const packageJson = JSON.parse(await readFile(packagePath, 'utf8'))
  packageJson.version = prepareVersion
  await writeFile(packagePath, `${JSON.stringify(packageJson, null, 2)}\n`, 'utf8')
  console.log(`已将 package.json 版本更新为 ${prepareVersion}`)
  process.exit(0)
}

const flavor = args.get('flavor')
const version = args.get('version')
const releaseNotes = args.get('releaseNotes')
const installerArg = args.get('installerPath')
if (!['owner', 'assistant'].includes(flavor) || !version || !SEMVER.test(version) || !releaseNotes?.trim() || !installerArg) throw new Error('用法: node scripts/release-update.mjs --flavor owner|assistant --version x.y.z --releaseNotes "说明" --installerPath 安装包.exe')
const packageJson = JSON.parse(await readFile(packagePath, 'utf8'))
if (packageJson.version !== version) throw new Error(`package.json version (${packageJson.version}) 与 --version (${version}) 不一致，请先运行 npm.cmd run prepare:release-version -- ${version}`)
const installerPath = resolve(installerArg)
const source = await stat(installerPath)
if (!source.isFile() || !installerPath.toLowerCase().endsWith('.exe')) throw new Error('installerPath 必须是存在的 .exe 安装包')
const outputDir = join(root, '..', 'server', 'static', 'updates', flavor)
await mkdir(outputDir, { recursive: true })
const fileName = `${flavor}-${version}-${basename(installerPath)}`
const destination = join(outputDir, fileName)
await copyFile(installerPath, destination)
const sha256 = createHash('sha256').update(await readFile(destination)).digest('hex')
const manifest = { flavor, version, releaseNotes: releaseNotes.trim(), publishedAt: new Date().toISOString(), downloadUrl: `/static/updates/${flavor}/${fileName}`, sha256, fileName }
await writeFile(join(outputDir, 'latest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
console.log(`已生成 ${join(outputDir, 'latest.json')}`)
