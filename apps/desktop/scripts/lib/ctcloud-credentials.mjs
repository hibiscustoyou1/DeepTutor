import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { appRoot } from './target.mjs';

// Fork-only credential loader shared by inject-ctcloud-secrets.mjs and
// build-with-secrets.mjs. Kept side-effect free so builds can fail fast and
// tests can import it on any platform.

const REPO_ROOT = path.join(appRoot, '../..');

function parseEnvFile(text) {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

/**
 * Resolve CTCLOUD_APP_CODE / CTCLOUD_APP_SECRET from the environment, falling
 * back to `KEY=VALUE` lines in the repo-root .env (git-ignored). Returns null
 * when neither source provides anything; both keys must come together.
 */
export async function loadCredentials(env = process.env) {
  let code = env.CTCLOUD_APP_CODE;
  let secret = env.CTCLOUD_APP_SECRET;
  if (!code || !secret) {
    try {
      const fileValues = parseEnvFile(await readFile(path.join(REPO_ROOT, '.env'), 'utf8'));
      code = code || fileValues.CTCLOUD_APP_CODE;
      secret = secret || fileValues.CTCLOUD_APP_SECRET;
    } catch { /* no .env — fine, env vars alone may carry the values */ }
  }
  if (code || secret) {
    if (!code || !secret) {
      throw new Error('CTCLOUD_APP_CODE and CTCLOUD_APP_SECRET must be provided together (env vars or repo-root .env)');
    }
    return { code, secret };
  }
  return null;
}
