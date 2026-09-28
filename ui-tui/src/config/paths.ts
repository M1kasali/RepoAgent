import { homedir } from 'node:os'
import { join } from 'node:path'

export const getPicoHome = (env: NodeJS.ProcessEnv = process.env) => env.REPOAGENT_HARNESS_HOME?.trim() || join(homedir(), '.repoagent-harness')

export const getPicoHomeLabel = (env: NodeJS.ProcessEnv = process.env) => env.REPOAGENT_HARNESS_HOME?.trim() || '~/.repoagent-harness'
