#!/usr/bin/env node
import { existsSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const androidDir = resolve(__dirname, '..', 'android')
const localPropsPath = resolve(androidDir, 'local.properties')

function isValidSdkDir(p) {
  if (!p || !existsSync(p)) return false
  return existsSync(join(p, 'platform-tools')) || existsSync(join(p, 'platforms')) || existsSync(join(p, 'cmdline-tools'))
}

function detectSdkDir() {
  const fromEnv = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT
  const candidates = [
    fromEnv,
    join(homedir(), 'android-sdk'),
    join(homedir(), 'Android', 'Sdk'),
    '/opt/android-sdk',
    '/usr/local/android-sdk',
  ].filter(Boolean)
  return { sdkDir: candidates.find(isValidSdkDir) || null, fromEnv, candidates }
}

function writeLocalProperties(sdkDir) {
  mkdirSync(androidDir, { recursive: true })
  writeFileSync(localPropsPath, `sdk.dir=${sdkDir}\n`, 'utf8')
  console.log(`[mobile:setup] Wrote android/local.properties (sdk.dir=${sdkDir}).`)
}

const platformExists = existsSync(resolve(androidDir, 'build.gradle'))

if (platformExists) {
  // Platform already scaffolded - just make sure local.properties points at the SDK.
  if (existsSync(localPropsPath)) {
    console.log('[mobile:setup] frontend/android/ already exists, skipping `cap add android`.')
    process.exit(0)
  }
  const { sdkDir, candidates } = detectSdkDir()
  if (!sdkDir) {
    console.error('[mobile:setup] Could not find an Android SDK and android/local.properties does not exist.')
    console.error('  Tried:')
    for (const c of candidates) console.error(`    - ${c}`)
    console.error('  Fix one of:')
    console.error('    1. export ANDROID_HOME=/path/to/android-sdk     (recommended)')
    console.error('    2. Install the SDK in one of the locations above')
    console.error('    3. Manually create frontend/android/local.properties with:')
    console.error('         sdk.dir=/path/to/android-sdk')
    process.exit(1)
  }
  writeLocalProperties(sdkDir)
  if (!process.env.ANDROID_HOME && !process.env.ANDROID_SDK_ROOT) {
    console.warn(`[mobile:setup] Tip: add \`export ANDROID_HOME=${sdkDir}\` to your shell rc.`)
  }
  console.log('[mobile:setup] frontend/android/ already exists, skipping `cap add android`.')
  process.exit(0)
}

// Platform does not exist yet - we need the SDK path before we can `cap add android`,
// but we can only WRITE local.properties after the dir exists. So: validate first,
// then scaffold, then write.
const { sdkDir, fromEnv, candidates } = detectSdkDir()
if (!sdkDir) {
  console.error('[mobile:setup] Could not find an Android SDK. `cap add android` needs the SDK to be set up.')
  console.error('')
  console.error('  Tried:')
  for (const c of candidates) console.error(`    - ${c}`)
  console.error('')
  console.error('  Fix one of:')
  console.error('    1. export ANDROID_HOME=/path/to/android-sdk     (recommended)')
  console.error('    2. Install the SDK in one of the locations above')
  process.exit(1)
}

console.log('[mobile:setup] Scaffolding frontend/android/ ...')
const r = spawnSync('npx', ['cap', 'add', 'android'], { stdio: 'inherit', cwd: resolve(__dirname, '..') })
if (r.status !== 0) process.exit(r.status ?? 1)

writeLocalProperties(sdkDir)
if (!fromEnv) {
  console.warn(`[mobile:setup] ANDROID_HOME was not set; auto-detected SDK at ${sdkDir}.`)
  console.warn(`[mobile:setup] Tip: add \`export ANDROID_HOME=${sdkDir}\` to your shell rc.`)
}
