import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { appRoot } from './lib/target.mjs';
import { loadCredentials } from './lib/ctcloud-credentials.mjs';

// Fork-only convenience wrapper (not part of the upstream build): builds the
// bundled desktop app with the ctcloud credentials baked in. Chains the
// upstream scripts unmodified:
//
//   1. build-runtime.mjs        — assemble the runtime (placeholders apply)
//   2. inject-ctcloud-secrets.mjs — write _ctcloud_secrets.py into the core
//   3. seal-runtime.mjs         — re-tar and re-sign (manifest must cover the
//                                 injected file, hence the second seal)
//   4. smoke.mjs                — boot the sealed runtime
//   5. package.mjs              — produce the DMG / installer
//
// Usage: node scripts/build-with-secrets.mjs --target <id> [--new-key]
//
// Credentials: CTCLOUD_APP_CODE / CTCLOUD_APP_SECRET via env vars or the
// repo-root .env (see inject-ctcloud-secrets.mjs). The runtime signing key
// follows the upstream convention: OPEN_DEEPTUTOR_SIGNING_KEY, or the
// existing .local/signing-private.pem, or --new-key for a throwaway identity.
//
// ctmeta.exe is NOT bundled: the ctcloud channel on Windows reads it from the
// cloud-desktop client's fixed install path (see ctcloud.py), so a real cloud
// desktop always has it.

const flag = process.argv.indexOf('--target');
const targetArg = flag >= 0 ? process.argv[flag + 1] : undefined;
if (!targetArg) {
  console.error('Usage: node scripts/build-with-secrets.mjs --target <id> [--new-key]');
  process.exit(1);
}
const newKey = process.argv.includes('--new-key');
// Fail fast: never burn a long build when credentials are missing.
const credentials = await loadCredentials();
if (!credentials) {
  console.error('Missing CTCLOUD_APP_CODE / CTCLOUD_APP_SECRET (env vars or repo-root .env).');
  process.exit(1);
}

const here = name => path.join(appRoot, 'scripts', name);
const step = (index, label, script, ...args) => {
  console.log(`\n=== [${index}/5] ${label} ===`);
  execFileSync(process.execPath, [here(script), ...args], { stdio: 'inherit' });
};

step(1, 'build:runtime (upstream)', 'build-runtime.mjs', '--target', targetArg, ...(newKey ? ['--new-key'] : []));
step(2, 'inject ctcloud credentials', 'inject-ctcloud-secrets.mjs', '--target', targetArg);
step(3, 're-seal runtime (upstream)', 'seal-runtime.mjs', '--target', targetArg);
step(4, 'smoke test (upstream)', 'smoke.mjs', '--target', targetArg);
step(5, 'package (upstream)', 'package.mjs', '--target', targetArg);
console.log(`\nBuilt ${targetArg} with injected ctcloud credentials.`);
