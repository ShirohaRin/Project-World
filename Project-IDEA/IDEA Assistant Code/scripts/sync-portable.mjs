import { cpSync, existsSync, renameSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'

const flavor = process.argv[2]
const targets = {
  assistant: 'IDEA Assistant',
  owner: 'IDEA Assistant SRH',
}

if (!(flavor in targets)) throw new Error('Expected assistant or owner flavor')

const source = resolve('..', 'build-output', flavor === 'owner' ? 'IDEA-Assistant-SRH' : 'IDEA-Assistant', 'win-unpacked')
const destination = resolve('..', targets[flavor])
const executableName = flavor === 'owner' ? 'IDEA.exe' : 'IDEA Assistant.exe'
const stagingPath = `${destination}.staging`
const backupPath = `${destination}.replace-backup`

function assertCompleteBuild(sourcePath, executableName) {
  const requiredPaths = [
    join(sourcePath, executableName),
    join(sourcePath, 'resources', 'app.asar'),
    join(sourcePath, 'locales', 'en-US.pak'),
  ]

  const missingPath = requiredPaths.find((path) => !existsSync(path))
  if (missingPath) {
    throw new Error(`Portable package is incomplete: ${missingPath}`)
  }
}

let replacementCompleted = false

try {
  if (!existsSync(source)) throw new Error(`Portable package does not exist: ${source}`)

  assertCompleteBuild(source, executableName)
  rmSync(stagingPath, { recursive: true, force: true })
  rmSync(backupPath, { recursive: true, force: true })
  cpSync(source, stagingPath, { recursive: true })
  assertCompleteBuild(stagingPath, executableName)

  if (existsSync(destination)) renameSync(destination, backupPath)
  try {
    renameSync(stagingPath, destination)
    replacementCompleted = true
    rmSync(backupPath, { recursive: true, force: true })
  } catch (error) {
    if (!existsSync(destination) && existsSync(backupPath)) renameSync(backupPath, destination)
    throw error
  }
} finally {
  rmSync(stagingPath, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 })
  if (replacementCompleted) rmSync(backupPath, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 })
  try {
    rmSync(source, { recursive: true, force: true, maxRetries: 8, retryDelay: 500 })
  } catch (error) {
    // The formal client is already atomically replaced; an external indexer may briefly retain the disposable build directory.
    console.warn(`Portable build cleanup deferred: ${source}`, error)
  }
}
