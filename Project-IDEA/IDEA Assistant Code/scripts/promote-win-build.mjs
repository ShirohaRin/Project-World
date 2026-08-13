import { existsSync } from 'node:fs';
import { rename, rm } from 'node:fs/promises';
import { resolve } from 'node:path';

const [targetArg, stagingArg] = process.argv.slice(2);
if (!targetArg || !stagingArg) {
  throw new Error('Usage: node scripts/promote-win-build.mjs <target-directory> <staging-directory>');
}

const target = resolve(targetArg);
const staging = resolve(stagingArg);
const unpacked = resolve(staging, 'win-unpacked');
if (!existsSync(unpacked)) {
  throw new Error(`Missing unpacked build output: ${unpacked}`);
}

const backup = `${target}.previous`;
if (existsSync(backup)) {
  await rm(backup, { recursive: true, force: true });
}
if (existsSync(target)) {
  await rename(target, backup);
}

await rename(unpacked, target);
await rm(staging, { recursive: true, force: true });
