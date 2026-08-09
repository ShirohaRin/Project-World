import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const flavor = process.argv[2]
if (!['assistant', 'owner'].includes(flavor)) throw new Error('Expected assistant or owner flavor')

const binary = (name) => join('node_modules', '.bin', process.platform === 'win32' ? `${name}.cmd` : name)
const run = (name, ...args) => execFileSync(binary(name), args, { stdio: 'inherit', shell: process.platform === 'win32', env: { ...process.env, IDEA_CLIENT_FLAVOR: flavor } })

run('tsc')
run('vite', 'build', '--mode', flavor)
run('tsc', '-p', 'tsconfig.electron.json')
mkdirSync('dist-electron', { recursive: true })
writeFileSync('dist-electron/client-flavor.json', JSON.stringify({ flavor }))
