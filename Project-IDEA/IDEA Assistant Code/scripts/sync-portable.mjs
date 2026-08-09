import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

const flavor = process.argv[2]
const targets = {
  assistant: 'IDEA Assistant',
  owner: 'IDEA Assistant SRH',
}

if (!(flavor in targets)) throw new Error('Expected assistant or owner flavor')

const source = resolve('..', 'release', flavor === 'owner' ? 'IDEA-Owner' : 'IDEA-Assistant', 'win-unpacked')
const destination = resolve('..', targets[flavor])

if (!existsSync(source)) throw new Error(`Portable package does not exist: ${source}`)

rmSync(destination, { recursive: true, force: true })
mkdirSync(destination, { recursive: true })
cpSync(source, destination, { recursive: true })
