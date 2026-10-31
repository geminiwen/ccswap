# ccswap
A lightweight, cross-platform command-line tool for managing multiple Claude Code configuration profiles. It allows users to easily switch between different sets of configurations, such as API keys, endpoints, and model mappings, using simple commands like `ccswap use <profile>`. The tool supports both API key and OAuth2 authentication methods.

**Version:** 2.0.0 (with OAuth2 Device Code Flow support)

Built with ❤️ by JUAN MACKIE

-------------

## ccacc account aliases and timezones

`ccacc` switches official Claude Code accounts separately from `ccswap` profiles.
Run it with `bash ./ccacc` from this checkout, or install it as an executable on
your PATH. Deploy **both `ccacc` and `ccacc_shell.sh` in the same directory**;
the companion is sourced by the core and does not need executable permission.
For example:

```bash
mkdir -p "$HOME/bin"
install -m 700 ccacc "$HOME/bin/ccacc"
install -m 600 ccacc_shell.sh "$HOME/bin/ccacc_shell.sh"
```

The account commands require `jq` and `openssl`. The shell hook supports Bash
and Zsh and requires a regular absolute installation path without symlink
components. It also works when `ccacc` is not executable: run `bash /path/to/ccacc`.

```bash
ccacc install                              # Install once, before any account setup
ccacc save personal                         # Choose timezone on first setup; optionally back up its alias
ccacc save work --alias work-claude         # Select a different alias to back up
ccacc save work --timezone Australia/Sydney # Choose the timezone explicitly
ccacc new --timezone Europe/London          # Clean to an unnamed logged-out session
claude /login                              # Sign in using the pending session timezone
ccacc save personal                        # Name and store the newly logged-in account
ccacc alias work work-claude                # Back up the alias without saving credentials
ccacc timezone work                        # Print the saved timezone
ccacc timezone                             # Default to the active saved account
ccacc timezone work Australia/Sydney        # Set timezone metadata only
```

`save <name>` accepts `--alias <alias-name>` and `--timezone <ZONE>` in either order.
An explicit `--timezone` takes priority, followed by an existing named choice in
`.timezones`. With no active profile or prior named choice, save can consume the
explicit pending-session timezone selected by `new`. Otherwise first setup requires
a numbered choice: UTC, Asia/Shanghai, Asia/Tokyo, Australia/Sydney, Europe/London,
America/New_York, America/Los_Angeles, or a custom IANA timezone entry.
There is no default or automatic detection from aliases, environment `TZ`, system
settings or account names. Invalid input, an empty choice or EOF fails immediately
before password prompts, credential changes, process shutdown, active-account changes or metadata
writes. Invalid, missing, duplicate and unknown flags also fail before any writes.
Save/new/use first check the existing shell installation without writing it, before
any timezone prompts, passwords, metadata, process shutdown or credential changes.
Missing, corrupt or stale installations fail with `ccacc install` instructions. `use` requires a valid saved timezone;
set one with `ccacc timezone <account> <ZONE>` before switching an older account.

Resolved timezones are stored independently as one zone per private
`~/.claude/accounts/.timezones/<account>` file (mode `600`, directory `700`).
An account can have a timezone without an alias backup. The setter
`ccacc timezone <account> <ZONE>` validates and writes only this metadata; it
does not require an existing saved account, Claude config, password or shell probe.

Alias backup queries the alias builtin in a new login/interactive Bash or Zsh
using `$SHELL` (Bash when unset). The shell's normal startup files run during
that query; the captured alias body is never evaluated or executed. Shell
startup/logout output is ignored. Missing aliases and failed probes leave any
existing alias backup unchanged. `ccacc alias` skips unsupported shells with a warning;
save/new/use fail if their required shell installation is invalid.
An absent alias creates no alias-definition backup. Save can still proceed
with a saved timezone, `--timezone`, or a valid prompted choice.

Backups are JSON in `~/.claude/accounts/.aliases/<account>.json`, written atomically with
mode `600`. They contain only `aliasName` and the original `definition`.
The hidden `.aliases` directory cannot be an account name and stays separate
from account history stashes. For example:

```json
{
  "aliasName": "work-claude",
  "definition": "TZ='Asia/Tokyo' claude"
}
```

Alias definitions are raw backups and never provide an account timezone, even
when they contain a literal `TZ`. New backups do not extract or store `env.TZ`.
Legacy alias records may remain on disk; their `env.TZ` is ignored. Zones selected
explicitly must use safe timezone-name characters and exist as files under
`/usr/share/zoneinfo`.

`ccacc timezone <name>` reads only that profile's independent `.timezones` metadata.
The no-argument getter reads the active profile's zone; only when no profile is
active can it read the pending session zone. A named query never falls back to
pending metadata or an alias. The getter never prompts,
starts a shell, asks for a master password, accesses credentials, stops processes,
or changes account state. It
prints one validated timezone and a newline on success. Missing or invalid TZ
returns nonzero with no stdout. `ccacc rm <account>` also removes that account's alias backup and
independent timezone metadata; metadata alone does not make an account appear
in `ls` or `current`.

`ccacc new [--timezone <ZONE>]` takes no profile name or alias flag. It first asks
whether you already ran `ccacc save <name>` to back up the current account. The
default is No: empty input, EOF and any answer other than `y`, `Y` or `yes` cancel
before credentials are read, processes stopped, or session/account metadata written.
New never automatically saves credentials or creates a named profile. After an
explicit confirmation, it can discard an unsaved login too.

Confirmed cleanup removes the current default Claude login by field from Keychain
and an optional `.credentials.json`, retaining MCP OAuth and other fields. It resets
IDs, account caches, project history/trust and counters; meaningful project MCP
configuration and server selections, including explicit `false`, are preserved.
It clears the enumerated account history/cache paths and runtime directories
(`session-env`, `tasks`, `sessions`, `ide`, `daemon`, `jobs`) only after safe path and
process checks. Ancestor processes are never signaled. Live or unverifiable owners,
symlinked cleanup paths, malformed optional credentials or stash conflicts stop the
operation with an incomplete/error result, rather than reporting a full cleanup.
Recognized timestamped `claude_...` recovery copies in `~/.claude_backups` are
cleared; unrecognized backup names and all named ccacc profile backups remain.

Cleanup checks writable config, metadata and history parents before stopping
processes. It stages the bounded history/cache paths, including recognized cleaner
recovery copies, until credentials, config and pending-session metadata succeed.
On failure it restores the prior history, config, credentials and session markers
where possible; Keychain snapshots stay in memory. No cleanup-success message is
printed for a failed write.

If rollback cannot finish, one protected directory at
`~/.claude/accounts/.cleanup-recovery` retains the remaining data and blocks
`save`, `new` and `use`, including the already-active shortcut. Its `manifest.json`
lists original `source`, temporary `staged` and optional profile-stash `saved`
paths. Restore each missing source from its remaining staged/saved copy without
overwriting existing data, and restore `config.json`, `active` and
`pending-timezone` to their original locations when those copies are present.
After checking the restored files, remove the recovery directory; a credential
restore failure requires the existing encrypted profile or a fresh login.
Recovery files contain no new plaintext Keychain snapshot. A process interruption
also leaves this guard in place for inspection instead of claiming an account is
already active.

Saved profiles, their independent timezones and Surge settings, MCP/shared device
keys, settings, skills, plugins, knowledge, hooks, other config directories and
Desktop state remain intact. When the active saved profile's identity matches the
current account, live history (including feedback drafts) moves into that existing
profile's stash without overwriting saved backups. A mismatched marker never writes
another profile's history. New keeps the current Surge route; profile policy setup
happens only when saving a named profile. The external `cccleaner` tool is not invoked.

New sessions require an explicit `--timezone` or numbered menu choice, never an
inferred/default zone. The choice is private pending metadata in
`~/.claude/accounts/.pending-timezone` (mode `600`). Successful cleanup removes
`.active` and creates no named profile. The generated launcher reads the pending
zone so `claude /login` works before a profile has a name. Then `ccacc save <name>`
backs up the login, activates the named profile and clears pending metadata only
after a successful save and activation. Repeated cleanup still requires backup
confirmation, and can reuse the explicitly selected pending timezone.
From a clean logged-out pending session, `ccacc use <saved-name>` restores an
existing profile directly and clears pending metadata without creating an empty
profile. Once the new session has a Claude login or unowned history, `use` refuses
until `ccacc save <name>` preserves it.

Run `ccacc install` explicitly, then **source the printed hook path once in your
current shell**. Installation requires only `jq`, Bash or Zsh, the adjacent companion
and safe startup paths. It works on a fresh HOME with no Claude config, account,
active marker, timezone, master password or credentials. It never asks for a timezone;
choose and save each account timezone when first running `save` or `new`.
A child `ccacc` process cannot update its calling shell. New interactive terminals
load the hook automatically. `configure-shell [account]` is a deprecated explicit
alias for `install`; its optional account argument is ignored.

`save`, `new` and `use` (including an already-active account) only check the hook,
private record, required original-alias backup and managed source entries in the
actual startup files. They never install or repair hook configuration. Valid
installations work with read-only startup files. Moved deployments, changed Zsh
startup directories, shadowed Bash login files and missing entries require an
explicit `ccacc install`. A modified hook is refused before any startup probe can
source it. Inspect that private hook and move it aside manually before rerunning
install; valid saved records allow the missing hook to be regenerated. Corrupt
private records require manual review. Existing valid version-1 records are
recognized without writes; explicit installation upgrades their records.
Zsh checks initialize a shell to discover its effective startup directory; Bash
checks read its known startup paths directly. Hook bytes are validated before
the Zsh startup probe can source them.

The hook reads `ccacc timezone` on every Claude launch, so switching the active
account updates TZ in an already-sourced shell. It also sets `BROWSER=false`.
It preserves literal command arguments, other leading assignments and supported
`env` / `_op_agent env` wrappers from the original `claude` alias. The first original
alias is stored privately in `~/.ccacc-shell/original-alias.json`; later installs
reuse it instead of backing up the generated alias. Arbitrary wrappers and dynamic
shell expressions fail explicitly rather than being evaluated. Captured alias
bodies stay on stdin between parsing tools, out of external process arguments.

The generated hook and original-alias record are private (directory `700`, files
`600`). Installation adds one managed source block at the end of Bash's `.bashrc`
and selected login file, or Zsh's `.zshrc` and `.zlogin`. Zsh's actual initialized
`${ZDOTDIR:-$HOME}` is used even when `.zshenv` sets `ZDOTDIR` without exporting it.
Repeat installation preserves unchanged startup/hook/record bytes, mtimes and inodes. Sourcing
the hook defines the launcher without starting Claude.

Run the fixture-only regressions from the repository root:

```bash
uv run tests/test_ccacc_alias_timezone.py
uv run tests/test_mcp_preservation.py
uv run tests/test_ccacc_shell.py
uv run tests/test_ccacc_shell_integration.py
```

These use isolated HOME directories, fixture shell startup/alias definitions, and
mocked process control, Keychain and Surge. They never switch a real account.
The integration suite uses the real core probe and real Bash/Zsh startup files,
with fake `_op_agent`, Claude and credential operations.

## Windows Installation

### Method 1: System32 (Easiest)
1. Download `ccswap.bat`
2. Copy to `C:\Windows\System32\ccswap.bat`
3. Open new Command Prompt and run: `ccswap`

### Method 2: Custom Tools Directory
1. Create `C:\tools` directory
2. Copy `ccswap.bat` to `C:\tools\ccswap.bat`
3. Add to PATH:
   - Press `Win + R`, type `sysdm.cpl`
   - Go to "Advanced" → "Environment Variables"
   - Edit "Path" → add `C:\tools`
4. Restart Command Prompt and run: `ccswap`

## Unix/Linux/macOS Installation

### Method 1: System-wide (Recommended)
```bash
# Make executable
chmod +x ccswap

# Move to system path
sudo mv ccswap /usr/local/bin/

# Test
ccswap help
```

### Method 2: User Local
```bash
# Create bin directory
mkdir -p ~/bin

# Move script
mv ccswap ~/bin/

# Add to PATH (add to ~/.bashrc or ~/.zshrc)
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Test
ccswap help
```

## Verification

After installation, verify it works:

```bash
# Windows
ccswap help

# Unix/Linux/macOS  
ccswap help
```

You should see the usage information if installed correctly.

## First Use

1. Run Claude Code at least once to create `settings.json`
2. Save your first profile: `ccswap save default`
3. Create additional profiles as needed

## Surge Proxy Switching (macOS)

Add a `surge` section to a profile to switch [Surge](https://nssurge.com) policy groups on `ccswap use`:

```
{
  "env": { ... },
  "surge": {
    "Claude": "US-Proxy",
    "Proxy": "HK-Proxy"
  }
}
```

- Keys are Surge policy group names, values are the policy to select in that group
- The policy must already be a member of the group in your Surge profile; otherwise it is skipped with a warning
- Profiles without a `surge` section leave Surge untouched
- Skipped with a warning when `surge-cli` is missing or Surge is not running

## Example profile for GLM 4.6
```
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "REDACTED-INSERT YOUR KEY",
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.5-air",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.6",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-4.6"
  }
}
```
## Example profile for Kimi K2-thinking
```
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "REDACTED-INSERT YOUR KEY",
    "ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-k2-thinking",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "kimi-k2-thinking",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "kimi-k2-thinking"
  }
}
```
## Example profile for MiniMax M2
```
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "REDACTED-INSERT YOUR KEY",
    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2"
  }
}
```

## File Structure After Installation

 
# Windows
%USERPROFILE%\.claude\
├── settings.json          # Active Claude Code config
├── profiles\              # Profile storage
│   ├── default.json
│   ├── work.json
│   └── personal.json
└── _current.txt          # Active profile tracker

# Unix/Linux/macOS
$HOME/.claude/
├── settings.json          # Active Claude Code config
├── profiles/              # Profile storage
│   ├── default.json
│   ├── work.json
│   └── personal.json
└── active_profile         # Active profile tracker
```

## Troubleshooting

### Windows Issues
- **"ccswap not recognized"**: Add to PATH or use full path
- **"Access denied"**: Run Command Prompt as Administrator
- **PowerShell issues**: Use Command Prompt instead

### Unix Issues
- **"Permission denied"**: Run `chmod +x ccswap`
- **"command not found"**: Add to PATH or use full path
- **"No such file"**: Create `~/.claude/` directory first

## Uninstallation

### Windows
```bash
# Remove from System32
del C:\Windows\System32\ccswap.bat

# Or remove from tools directory
del C:\tools\ccswap.bat
```

### Unix/Linux/macOS
```bash
# Remove from system
sudo rm /usr/local/bin/ccswap

# Or remove from user directory
rm ~/bin/ccswap
```

Your profiles and settings remain in `~/.claude/` for future use.

-------------
# OAuth2 Authentication

ccswap v2.0.0 adds support for OAuth2 Device Code Flow authentication, enabling you to use enterprise SSO or custom OAuth2 providers with Claude Code.

## Prerequisites for OAuth2

OAuth2 support requires additional tools:

### Linux
```bash
# Debian/Ubuntu
sudo apt-get install curl jq openssl

# RHEL/CentOS/Fedora
sudo dnf install curl jq openssl
```

### macOS
```bash
brew install curl jq openssl
```

### Windows
- `curl` - Built-in on Windows 10 1803+
- `PowerShell 5.1+` - Built-in on Windows 10+
- `jq` - Install via:
  ```powershell
  choco install jq
  ```
  Or download from: https://stedolan.github.io/jq/download/

## Creating an OAuth2 Profile

OAuth2 profiles include an `auth_type` field and an `oauth2` configuration object:

```json
{
  "auth_type": "oauth2",
  "oauth2": {
    "client_id": "your_client_id_here",
    "client_secret": "your_client_secret_here",
    "device_code_endpoint": "https://auth.example.com/oauth/device_code",
    "token_endpoint": "https://auth.example.com/oauth/token",
    "scopes": "openid profile email offline_access"
  },
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-3-haiku-20240307",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-3-5-sonnet-20241022",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-3-opus-20240229"
  }
}
```

### OAuth2 Profile Fields

| Field | Description | Required |
|-------|-------------|----------|
| `auth_type` | Must be `"oauth2"` | Yes |
| `oauth2.client_id` | OAuth2 client ID | Yes |
| `oauth2.client_secret` | OAuth2 client secret | Yes* |
| `oauth2.device_code_endpoint` | Device authorization endpoint | Yes |
| `oauth2.token_endpoint` | Token endpoint | Yes |
| `oauth2.scopes` | OAuth2 scopes (default: openid profile email offline_access) | No |

*Some OAuth2 providers (public clients) don't require a client secret for Device Code Flow.

## OAuth2 Commands

### Login

Authenticate a profile using OAuth2 Device Code Flow:

```bash
ccswap oauth login <profile>
```

**Example:**
```bash
ccswap oauth login work
```

**What happens:**
1. You'll be prompted to create an encryption password for storing OAuth tokens
2. A device code is obtained from your OAuth2 provider
3. You'll see instructions to visit a URL and enter a code
4. Once you complete authorization in your browser, tokens are automatically retrieved
5. Tokens are encrypted with your password and stored securely

### Status

Check the current OAuth2 token status:

```bash
ccswap oauth status <profile>
```

**Example:**
```bash
ccswap oauth status work
```

**Output:**
```
Enter password to decrypt OAuth tokens: ********

OAuth2 Token Status
===========================================
Authentication Type: OAuth2
Token Type: Bearer
Access Token: eyJhbGci...VzI1Ni
Status: Active
Expires: 45m 23s from now
Scopes: openid profile email offline_access
===========================================
```

### Logout

Remove OAuth2 credentials from a profile:

```bash
ccswap oauth logout <profile>
```

**Example:**
```bash
ccswap oauth logout work
```

This deletes the encrypted token file. The profile itself remains and can be re-authenticated.

## Using OAuth2 Profiles

OAuth2 profiles work seamlessly with existing ccswap commands:

### Switch to OAuth2 Profile

```bash
ccswap use work_oauth
```

**What happens:**
1. Detects that the profile uses OAuth2
2. Prompts for your token decryption password
3. Checks if the access token is expired
4. Automatically refreshes if needed (using the refresh token)
5. Updates `settings.json` with the fresh access token

### Save OAuth2 Profile

```bash
ccswap save work_backup
```

If the currently active profile is OAuth2, the encrypted tokens are also copied to the new profile.

### List Profiles

```bash
ccswap ls
```

OAuth2 profiles are marked with `[OAuth2]`:

```
Available profiles:
  * default
    work [OAuth2]
    personal [OAuth2] (active)
```

### Delete OAuth2 Profile

```bash
ccswap rm work
```

Both the profile JSON and the encrypted token file are deleted.

## Token Storage

OAuth2 tokens are stored encrypted in your profiles directory:

**Unix/Linux/macOS:**
```
~/.claude/profiles/
├── work.json          # Profile configuration
└── work_tokens.enc    # Encrypted OAuth tokens
```

**Windows:**
```
%USERPROFILE%\.claude\profiles\
├── work.json          # Profile configuration
└── work_tokens.enc    # Encrypted OAuth tokens
```

### Encryption Details

- **Algorithm:** AES-256-CBC
- **Key Derivation:** PBKDF2 with 100,000 iterations (HMAC-SHA256 via `openssl enc` on Unix/macOS, HMAC-SHA1 via .NET on Windows)
- **Portability:** Token files are not interchangeable between Unix/macOS and Windows; run `ccswap oauth login` on each machine
- **File Permissions:** 600 (user read/write only on Unix/macOS)
- **Password:** User-provided, not stored anywhere

## Token Refresh

OAuth2 access tokens typically expire after 1 hour. ccswap automatically handles token refresh:

1. When you use an OAuth2 profile, it checks if the token is expired (or within 5 minutes of expiration)
2. If expiring, it automatically uses the refresh token to get a new access token
3. The new token is re-encrypted and stored
4. No manual intervention required

If the refresh token is expired or invalid, you'll be prompted to re-authenticate:

```bash
ccswap oauth login work
```

## Mixed Profile Support

You can have both API key and OAuth2 profiles:

```
Available profiles:
  * default              # API key profile
    work                 # API key profile
    enterprise [OAuth2]  # OAuth2 profile
    personal             # API key profile
```

Switching between profile types works seamlessly:

```bash
ccswap use default        # Uses API key from profile
ccswap use enterprise     # Prompts for OAuth2 password, decrypts tokens
```

## Security Considerations

### Best Practices

1. **Strong Passwords:** Use a strong password for encrypting OAuth tokens
2. **HTTPS Only:** All OAuth2 endpoints must use HTTPS
3. **Secure Storage:** Token files have restrictive permissions (600 on Unix/macOS)
4. **Regular Rotation:** Consider running `ccswap oauth logout` then `ccswap oauth login` periodically to get fresh tokens

### What's Stored

| Location | Content | Encryption |
|----------|---------|------------|
| Profile JSON (`<name>.json`) | Client ID, client secret, endpoints | **No** (plain text) |
| Token file (`<name>_tokens.enc`) | Access token, refresh token | **Yes** (AES-256-CBC) |

### Why Client Secret in Plain Text?

Storing OAuth2 client credentials in the profile JSON is standard practice. These are not secret - they're meant to be distributed to client applications. The actual secrets (access tokens and refresh tokens) are encrypted.

## Troubleshooting OAuth2

### "Dependencies not found"

Install the required tools:
```bash
# Linux
sudo apt-get install curl jq openssl

# macOS
brew install curl jq openssl

# Windows (jq only)
choco install jq
```

### "Failed to decrypt tokens"

- Wrong password entered
- Token file corrupted
- Solution: Try again with correct password, or delete and re-authenticate

### "Token refresh failed"

- Refresh token expired or revoked
- OAuth2 provider issues
- Solution: Run `ccswap oauth login <profile>` to re-authenticate

### "Authorization timed out"

- Device code expired (usually 30 minutes)
- User didn't complete authorization in time
- Solution: Run `ccswap oauth login <profile>` again

### "Invalid OAuth2 configuration"

Check your profile JSON has all required fields:
- `auth_type`: "oauth2"
- `oauth2.client_id`
- `oauth2.device_code_endpoint`
- `oauth2.token_endpoint`

## Migration from API Key to OAuth2

To convert an existing API key profile to OAuth2:

1. **Backup your current profile:**
   ```bash
   cp ~/.claude/profiles/work.json ~/.claude/profiles/work.json.bak
   ```

2. **Edit the profile to add OAuth2 configuration:**
   ```bash
   # Add auth_type and oauth2 fields
   nano ~/.claude/profiles/work.json
   ```

   Change from:
   ```json
   {
     "env": {
       "ANTHROPIC_AUTH_TOKEN": "your-api-key",
       ...
     }
   }
   ```

   To:
   ```json
   {
     "auth_type": "oauth2",
     "oauth2": {
       "client_id": "your_client_id",
       "device_code_endpoint": "https://...",
       "token_endpoint": "https://...",
       "scopes": "openid profile email offline_access"
     },
     "env": {
       "ANTHROPIC_BASE_URL": "https://...",
       ...
     }
   }
   ```

3. **Authenticate:**
   ```bash
   ccswap oauth login work
   ```

4. **Use the profile:**
   ```bash
   ccswap use work
   ```

To revert back to API key authentication, simply remove the `auth_type` and `oauth2` fields from your profile JSON and restore the `ANTHROPIC_AUTH_TOKEN` in the `env` section.
