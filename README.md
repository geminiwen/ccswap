# ccacc

Switch official Claude Code accounts on macOS, keeping each account's
login, account-bound configuration, history and session files together.

## Requirements

- macOS only. Claude logins are read from and written to the macOS keychain
  (`security`, `osascript`); the installer and every command except `help` and
  `--version` refuse other platforms.
- Bash (the system Bash 3.2 works), `curl` and `jq`; account backups also
  require `openssl`.
- Bash or Zsh as the interactive shell for the launch hook.

## Installation

Install or update with the same command:

```bash
curl -fsSL https://raw.githubusercontent.com/geminiwen/ccswap/main/install.sh | bash
source "$HOME/.ccacc-shell/hook.sh"
```

The installer deploys `ccacc` and its required `ccacc_shell.sh` companion together
from one resolved commit, into `~/.local/bin` by default. It configures the hook
without requiring a saved account, passwords or Claude credentials. Source the
printed hook path once to activate it in the current shell, or open a new
interactive terminal. The hook adds the installed directory to PATH. Keep both
files together; moving them or changing shell startup files can require a reinstall.

To choose another absolute **bin directory**:

```bash
curl -fsSL https://raw.githubusercontent.com/geminiwen/ccswap/main/install.sh | bash -s -- --prefix /absolute/path/to/bin
```

Installation rejects symlinked targets or path components. It does not install
packages or require `sudo`. If deployment succeeds but shell setup fails, follow
the printed repair command or rerun the installer after resolving the error.

## Account workflow

```bash
ccacc save personal --timezone Asia/Shanghai # Save the current login and name it
ccacc new                                   # Confirm backup, choose optional TZ, log out
claude /login                               # Login in the unnamed session
ccacc save work                              # Choose work's saved timezone independently
ccacc use personal                          # Auto-back up outgoing account, choose YOLO, switch
ccacc use                                   # Pick a saved profile in the terminal
ccacc list                                  # List profiles; ls is an alias
ccacc current                               # Show current email and saved name
ccacc timezone                              # Read the current session timezone
ccacc timezone work                         # Read a saved profile's timezone
ccacc rm work                               # Confirm deletion of that profile and stash
ccacc help
```

`save <name>` creates a new profile or refreshes the **active** one under its own
name. If `<name>` already exists (its profile JSON, encrypted login, saved timezone
or history stash) and is not the active profile, save fails before any prompt or
change:

```text
Error: Account 'work' already exists and is not active; choose another name, or run 'ccacc use work' / 'ccacc rm work' first
```

Save/use share one master password for encrypted login backups. Save leaves
history and session directories in place while that account is active.
Deleting an active profile leaves its current login/history in place but removes
its saved identity, backup, timezone, policy choices and stored stash.

## Timezones and launches

After backup confirmation, `new` offers UTC, Asia/Shanghai, Asia/Tokyo,
Australia/Sydney, Europe/London, America/New_York, America/Los_Angeles, or a custom
IANA timezone. Blank or EOF means default: `TZ` is genuinely unset in the Claude
child process. `q` cancels; invalid input fails before process shutdown or account
changes. The command accepts no arguments and never infers a zone from aliases,
account names, host settings or the calling shell's `TZ`.

A selected zone applies immediately to the unnamed session, before login or save.
It does **not** choose the timezone for a later save. Save's priority is:

1. Explicit `ccacc save <name> --timezone <ZONE>`.
2. When re-saving the active profile, its existing timezone.
3. Its own required preset/custom picker, with no blank/default choice.

A successful save or use clears unnamed-session metadata; failed saves retain it.
Repeated `new` with a blank/default choice clears any previous selection. Saved
zones live in `~/.claude/accounts/.timezones/<name>`; an explicit unnamed selection
uses the private validated `.unnamed-session` record. Legacy raw
`.pending-timezone` is never trusted. The read-only `timezone` getter prints one
validated zone with status `0`, or returns empty output and status `3` for an
unnamed default. Invalid active metadata returns status `1` and blocks hooked launch.

On every hooked `claude` launch, the hook reads the current zone and forces
`BROWSER=false`. Only the child environment changes; the caller's `TZ` and
`BROWSER` stay unchanged. The hook calls the native `claude` executable on PATH,
bypassing aliases/functions and copying no old wrappers or fixed flags. It
forwards caller arguments and exit status, without prompting or reading stdin.

Only `use` requires a verified installed hook, including when choosing the active
profile. Save/new do not install, repair or require it, but still require the
companion's path-safety helpers. Without an activated hook, its launch environment
guarantees do not apply. Reinstall when the installed pair or managed hook is stale;
inspect modified/orphaned hook files before following repair instructions.

## Switching and permissions

No-name `use` opens a terminal picker: Up/Down or `j`/`k` moves, Return selects,
and `q`, Esc, Ctrl-C or EOF cancels. It requires terminal stdin and stderr.

Switching between saved accounts automatically saves the latest outgoing login
and account configuration, then archives its history/session/cache paths,
including `~/.claude/sessions`. After saving and archival succeed, it reports the
outgoing profile, encrypted login backup (or logged-out state), configuration
snapshot and actual archived paths on stderr, without printing their contents.
Named noninteractive switches need no backup confirmation:

```bash
ccacc use work --no-yolo
ccacc use work --yolo
```

`--yes` is still accepted as a compatibility no-op; it is no longer required,
never selects a profile and never enables YOLO. On a terminal, use separately asks
`YOLO? (y/N)` unless `--yolo` or `--no-yolo` is explicit. No/blank/EOF chooses normal
permissions; Ctrl-C cancels before account changes. Noninteractive use defaults
to normal permissions. YOLO adds `--dangerously-skip-permissions` to hooked Claude
launches while that profile is active; caller-supplied flags are still forwarded.
The choice belongs to the current selection, not the profile backup. Successful
save/new clear it, and unnamed sessions always use normal permissions.

Choosing the already-active profile changes its launch mode without reloading an
older backup. A clean logged-out unnamed session can use a saved profile directly;
unsaved login or unowned history/session files must be saved first, even with
`--yes`. An otherwise clean unnamed session may retain an empty sessions registry
directory; any entries, including hidden files or links, still require saving.
Switching stops Claude Code processes owned by the current user, including
those using other config directories; it leaves those other directories' credentials
and files unchanged. Fully quit Claude.app and its browser-extension helpers
before cleanup; ccacc warns and refuses cleanup while those owners remain live.

## Backup boundaries

| Operation | Saved or moved data |
| --- | --- |
| `save <name>` | Creates a new profile or refreshes the active one; other existing names are refused. Only `claudeAiOauth` goes into `<name>.credentials.enc`; account-bound `~/.claude.json` fields go into `<name>.json`. Save also records the independently chosen timezone and optional Surge policies. Live directories stay in place. |
| `use <name>` | Updates the outgoing login/config backup, moves its account directories into `~/.claude/accounts/<outgoing>/`, and restores the target's saved login/config and directory stash. Saved timezone and Surge choices stay unchanged. |
| `new` | Never auto-saves login/config or creates a named profile. After confirmation it logs out and resets account state. If the active saved identity matches the current identity, archive paths move into its stash; unmatched/unnamed paths are cleared. |

Only the credentials backup is encrypted (AES-256-CBC with PBKDF2). Profile JSON
and stashed files, including session JSON and `.key` bytes, are stored as files
without an additional encryption layer. Session archival preserves those bytes;
it does not restart stopped processes or recreate running sessions.

The archive includes these `~/.claude` paths:

```text
backups/  debug/  file-history/  feedback/drafts/  paste-cache/  plans/
projects/  sessions/  session-env/  shell-snapshots/  statsig/  tasks/
telemetry/  todos/  cache/  stats-cache.json  history.jsonl  gh-pr-status-cache.json
```

It also includes `~/Library/Caches/claude-cli-nodejs`. `feedback/drafts` is stored
under the stash label `feedback-drafts`; the external CLI cache uses
`cli-nodejs-cache`. `new` additionally clears bounded `ide`, `daemon`, `jobs`,
remote/policy cache files, plugin directory/catalog caches and recognized
timestamped cleaner recovery copies. Those extra runtime paths are not part of
the account archive.

MCP OAuth and unrelated credential fields stay shared and are never restored from
account login backups. Shared device keys, shared MCP configuration, settings, skills,
installed plugins, knowledge, shell hooks and Claude Desktop data remain in place.
Meaningful project MCP definitions/selections survive `new`, including explicit
`false`. Suffixed Keychain items, other Claude config directories and Cowork device
keys are outside the account backup. New keeps the current Surge route; when
Surge is available, save can record policies and use applies saved choices.

## Failures and recovery

New checks paths, writability, credentials, stash conflicts and live/unverifiable
runtime owners before cleanup. Symlinks or malformed metadata fail closed.
Cancellation leaves existing account data unchanged. Cleanup stages bounded paths
and snapshots config/session metadata, then restores them where possible if a
later step fails. Save/delete also stage their bounded profile metadata for rollback.

If recovery is incomplete, the error identifies either
`~/.claude/accounts/.cleanup-recovery` or a `.profile-transaction.*` directory.
These guards block further account changes and hooked launches. Inspect its
`manifest.json` and retained config/metadata copies; restore the recorded paths
without overwriting existing data before removing the guard. A failed credential
restore may require a saved encrypted profile or a fresh login. Keychain snapshots
stay in memory; cleanup does not create plaintext Keychain recovery files.

A late `use` failure can leave some target state applied. Read the error and inspect
the current account before retrying; the command does not claim a completed switch.
An earlier outgoing-backup report describes the saved backup only; it does not
mean that the target restore or switch completed.

## Development checks

Run from the repository root with temporary/cache paths under `.orca`:

```bash
mkdir -p .orca/uv-cache .orca/tmp .orca/test-home
HOME="$PWD/.orca/test-home" UV_CACHE_DIR="$PWD/.orca/uv-cache" TMPDIR="$PWD/.orca/tmp" uv run --no-project python -m unittest discover -s tests
HOME="$PWD/.orca/test-home" UV_CACHE_DIR="$PWD/.orca/uv-cache" TMPDIR="$PWD/.orca/tmp" uv run --no-project python tests/test_mcp_preservation.py
```

Tests use isolated homes, synthetic credentials/session keys, fake Claude launches
and mocked process/Keychain/Surge/network boundaries. They do not prove a live
Claude login or a production account switch.
