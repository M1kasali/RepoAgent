import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { getPicoHome, getPicoHomeLabel } from '../config/paths.js'

describe('RepoAgent product paths', () => {
  it('uses the RepoAgent home override for persistent TUI state', () => {
    expect(getPicoHome({ REPOAGENT_HARNESS_HOME: '/tmp/pico-home' })).toBe('/tmp/pico-home')
    expect(getPicoHomeLabel({ REPOAGENT_HARNESS_HOME: '/tmp/pico-home' })).toBe('/tmp/pico-home')
  })

  it('uses ~/.repoagent-harness when the override is empty', () => {
    expect(getPicoHome({ REPOAGENT_HARNESS_HOME: '  ' })).toBe(join(homedir(), '.repoagent-harness'))
    expect(getPicoHomeLabel({ REPOAGENT_HARNESS_HOME: '  ' })).toBe('~/.repoagent-harness')
  })
})
