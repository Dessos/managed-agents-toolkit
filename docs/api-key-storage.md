# API key storage — keeping the key out of plaintext

The toolkit resolves `ANTHROPIC_API_KEY` from a chain of sources, accepting
the first **real** key it finds:

1. `ANTHROPIC_API_KEY` env var (if set AND not a known sentinel)
2. `CMA_API_KEY_HELPER` env var — a shell command whose stdout is the key
3. `apiKeyHelper` field in `~/.claude/settings.json` — same semantics, set
   once and shared with Claude Code's native auth path

Sentinel detection rejects anything that doesn't look like a real key
(<30 chars, wrong prefix, or matches a known decoy like `sk-ant-..`,
`sk-ant-placeholder`, `sk-ant-xxxxxxxx`). This means you can leave a
defensive placeholder in your env without it leaking into actual API
calls.

## Why bother

Common patterns and the risks they carry:

| Pattern | Risk |
|---|---|
| `ANTHROPIC_API_KEY=sk-ant-real-key...` in `.env` | Key sits unencrypted on disk, visible to any process running as the user. Often committed by accident. |
| Same in shell profile (`.bashrc`, PowerShell profile) | Same plus: every subprocess inherits it (sometimes including ones we don't want it in). |
| User-scope Windows env var via System Properties | Encrypted by DPAPI per-user but `Get-Item Env:` reads it transparently. |
| **Secret manager + `apiKeyHelper` (this guide)** | Key never in plain disk/env. Fetched at API-call time, cached in process memory for the lifetime of one invocation. Rotation = update the secret once. |

## Setup — Windows (PowerShell + SecretManagement)

One-time setup:

```powershell
# Install the modules (current user; no admin needed)
Install-Module Microsoft.PowerShell.SecretManagement -Scope CurrentUser
Install-Module Microsoft.PowerShell.SecretStore         -Scope CurrentUser

# Register the local SecretStore as the default vault
Register-SecretVault -Name CmaSecrets `
                     -ModuleName Microsoft.PowerShell.SecretStore `
                     -DefaultVault

# First call prompts for a master password (used to unlock the store)
Set-SecretStoreConfiguration -Authentication Password `
                             -PasswordTimeout 28800 `
                             -Interaction None

# Store the actual key (you'll be prompted to type the password set above)
Set-Secret -Name AnthropicApiKey -Secret 'sk-ant-api03-YOUR_REAL_KEY_HERE'

# Verify
Get-SecretInfo -Name AnthropicApiKey
# → Name: AnthropicApiKey, VaultName: CmaSecrets, Type: SecureString
```

Wire it into cma:

```powershell
# Option A: env var (per-shell or in your PowerShell profile)
$env:CMA_API_KEY_HELPER = 'powershell -NoProfile -Command "Get-Secret AnthropicApiKey -AsPlainText"'

# Option B: persist into ~/.claude/settings.json (shared with Claude Code)
$settingsPath = Join-Path $env:USERPROFILE '.claude\settings.json'
$settings = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw | ConvertFrom-Json } else { @{} | ConvertTo-Json | ConvertFrom-Json }
$settings | Add-Member -NotePropertyName 'apiKeyHelper' -NotePropertyValue 'powershell -NoProfile -Command "Get-Secret AnthropicApiKey -AsPlainText"' -Force
$settings | ConvertTo-Json -Depth 16 | Set-Content $settingsPath -Encoding utf8
```

Verify cma picks it up:

```powershell
python -m cma.cli doctor --probe-beta
```

The doctor command will resolve the key via the helper chain, run the probe,
and report alpha access state. If the key isn't found, the error message
tells you which source failed and why.

## Setup — macOS (Keychain)

```bash
# Store
security add-generic-password -a "$USER" -s anthropic-api-key -w 'sk-ant-api03-YOUR_REAL_KEY_HERE'

# Wire (~/.zshrc or per-session)
export CMA_API_KEY_HELPER='security find-generic-password -a "$USER" -s anthropic-api-key -w'

# Or in ~/.claude/settings.json
# "apiKeyHelper": "security find-generic-password -a $USER -s anthropic-api-key -w"
```

## Setup — Linux (libsecret / GNOME Keyring)

```bash
# Store (interactive prompt for the value)
secret-tool store --label="Anthropic API Key" service anthropic-api-key

# Wire
export CMA_API_KEY_HELPER='secret-tool lookup service anthropic-api-key'

# Or in ~/.claude/settings.json
# "apiKeyHelper": "secret-tool lookup service anthropic-api-key"
```

## Rotation

Update the secret in your store; cma restarts will pick up the new value:

```powershell
Set-Secret -Name AnthropicApiKey -Secret 'sk-ant-api03-NEW_KEY_HERE'
# No code change. No env-var edits. Restart any long-running cma daemon.
```

Anything that reads the value at startup (one-shot CLI invocations like
`cma doctor`) picks up the new key on the next run. Long-running daemons
(`cma executor serve`) cache the resolved key for the lifetime of the
process — restart the daemon after rotation.

## Sentinel pattern (operator's existing setup)

If you currently have an `ANTHROPIC_API_KEY` env var set to a short
defensive value like `sk-ant-..`, the toolkit detects this as a sentinel
and falls through to the helper chain. You can keep the sentinel in place
as a "yes I considered API key storage" canary while the real key lives in
your secret manager. No change required to the env var itself.

## Verifying the chain end-to-end

```powershell
# 1. Confirm the resolved key looks real (length only — never print value)
python -c "from cma.api.client import _api_key; k = _api_key(); print(f'Resolved: {len(k)} chars, starts with {k[:8]!r}')"

# 2. Run the doctor probe (actual API call)
python -m cma.cli doctor --probe-beta
```

If step 1 succeeds and step 2 returns a meaningful beta-access state, the
chain is wired correctly.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Could not resolve a valid Anthropic API key` | None of the three sources yielded a real key. | Check the error message — each source's rejection reason is listed. |
| `apiKeyHelper command timed out after 10s` | First call to PowerShell + SecretStore can be slow on cold start; usually only at boot. | Re-run; if persistent, run the helper command directly in PowerShell to debug. |
| `apiKeyHelper command exited 1` | Secret store locked (password timeout expired) or secret name typo. | `Unlock-SecretStore` and / or check `Get-SecretInfo`. |
| `apiKeyHelper command produced empty stdout` | Helper command ran but returned nothing. | Run it directly; check vault binding. |
| Doctor probe returns 401 | Resolved key was valid-looking but actually wrong. | Verify the secret value matches your Anthropic console key. |
