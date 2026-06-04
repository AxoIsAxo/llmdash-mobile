#!/usr/bin/env node
import { existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const androidDir = resolve(__dirname, '..', 'android')

if (existsSync(resolve(androidDir, 'build.gradle'))) {
  console.log('[mobile:setup] frontend/android/ already exists, skipping `cap add android`.')
  console.log('[mobile:setup] To re-scaffold from scratch, delete frontend/android/ and re-run this command.')
  process.exit(0)
}

console.log('[mobile:setup] Scaffolding frontend/android/ ...')
const r = spawnSync('npx', ['cap', 'add', 'android'], { stdio: 'inherit' })
process.exit(r.status ?? 1)
