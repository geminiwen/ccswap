import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'ccacc').read_text().rsplit('main "$@"', 1)[0]
BASE = {
    'claudeAiOauth': {'accessToken': 'fixture-old', 'refreshToken': 'fixture-refresh'},
    'mcpOAuth': {'figma': {'accessToken': 'fixture-shared', 'refreshToken': 'fixture-mcp-refresh'}},
    'futureField': {'nested': [1, None, False]},
}
TARGET = {
    'claudeAiOauth': {'accessToken': 'fixture-new'},
    'mcpOAuth': {'other': {'accessToken': 'fixture-must-not-restore'}},
    'futureField': 'fixture-must-not-restore',
}
MOCKS = r'''
command() {
    if [ "$BACKEND" = linux ] && [ "$1" = -v ] && [ "$2" = security ]; then return 1; fi
    builtin command "$@"
}
security() {
    if [ "$1" = delete-generic-password ]; then
        printf 'delete\n' >> "$MUTATIONS"
        rm -f "$STATE"
    else
        echo 'Unexpected security invocation' >&2
        return 99
    fi
}
keychain_read() {
    [ "${READ_FAIL:-}" != yes ] || return 1
    if [ -f "$STATE" ]; then cat "$STATE"; fi
}
keychain_write() {
    printf 'write\n' >> "$MUTATIONS"
    printf '%s' "$3" > "$STATE"
}
ask_password() { :; }
require_shell_hook() { :; }
load_shell_companion() { :; }
ccacc_check_shell_hook() { return 1; }
cleanup_stop_processes() { stop_claude; }
cleanup_runtime_is_safe() { :; }
_ccacc_shell_path() { :; }
stop_claude() { :; }
note_device_keys() { :; }
setup_surge() { :; }
apply_surge() { :; }
MASTER_PASSWORD=fixture-master
'''


def main():
    count = 0
    (ROOT / '.orca').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / '.orca', prefix='mcp-preservation-') as root:
        root = Path(root).resolve()
        for backend in ('macos', 'linux'):
            counter = 0

            def run(body, current=BASE, target=TARGET, read_fail=False, expect_success=True):
                nonlocal count, counter
                counter += 1
                fixture = root / (backend + '-' + str(counter))
                fixture.mkdir()
                home = fixture / 'home'
                (home / '.claude/accounts').mkdir(parents=True)
                state = fixture / 'keychain.json' if backend == 'macos' else home / '.claude/.credentials.json'
                state.write_text(current if isinstance(current, str) else json.dumps(current))
                (fixture / 'target.json').write_text(target if isinstance(target, str) else json.dumps(target))
                (fixture / 'mutations').write_text('')
                (home / '.claude.json').write_text(json.dumps({
                    'oauthAccount': {'emailAddress': 'fixture@example.invalid'},
                    'userID': 'fixture-original-id',
                    'mcpServers': {'fixture': {'type': 'http'}},
                    'projects': {},
                }))
                env = dict(os.environ, HOME=str(home), TMPDIR=str(fixture), STATE=str(state),
                           MUTATIONS=str(fixture / 'mutations'), FIXTURE=str(fixture),
                           BACKEND=backend, READ_FAIL='yes' if read_fail else '')
                env.pop('BASH_ENV', None)
                env.pop('ENV', None)
                result = subprocess.run(['bash', '-c', SOURCE + '\n' + MOCKS + '\n' + body],
                                        env=env, capture_output=True, text=True, input="yes\n")
                assert (result.returncode == 0) == expect_success, (backend, body, result.stdout, result.stderr)
                count += 1
                return fixture, state

            for target in (TARGET, {'claudeAiOauth': TARGET['claudeAiOauth']}):
                _, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"', target=target)
                expected = {**BASE, 'claudeAiOauth': TARGET['claudeAiOauth']}
                assert json.loads(state.read_text()) == expected

            for target in ('', {}, {'mcpOAuth': TARGET['mcpOAuth']}, {'claudeAiOauth': None}):
                _, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"', target=target)
                assert json.loads(state.read_text()) == {k: v for k, v in BASE.items() if k != 'claudeAiOauth'}

            _, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"', current='',
                           target={'claudeAiOauth': TARGET['claudeAiOauth']})
            assert json.loads(state.read_text()) == {'claudeAiOauth': TARGET['claudeAiOauth']}

            _, state = run('replace_credentials ""', current={'claudeAiOauth': BASE['claudeAiOauth']})
            assert not state.exists()

            for invalid in ('{invalid', '[]', 'null', '42', '{} {}', {'claudeAiOauth': 'invalid'}):
                fixture, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"',
                                     target=invalid, expect_success=False)
                assert json.loads(state.read_text()) == BASE
                assert (fixture / 'mutations').read_text() == ''

            for invalid in ('{invalid', '[]', 'null', '{} {}'):
                fixture, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"',
                                     current=invalid, expect_success=False)
                assert state.read_text() == invalid
                assert (fixture / 'mutations').read_text() == ''

            if backend == 'macos':
                fixture, state = run('replace_credentials "$(cat "$FIXTURE/target.json")"',
                                     read_fail=True, expect_success=False)
                assert json.loads(state.read_text()) == BASE
                assert (fixture / 'mutations').read_text() == ''

            fixture, _ = run(r'''
write_backup example credentials "$(cat "$FIXTURE/target.json")"
read_backup example credentials > "$FIXTURE/decrypted.json"
''')
            assert json.loads((fixture / 'decrypted.json').read_text()) == {'claudeAiOauth': TARGET['claudeAiOauth']}

            fixture, _ = run(r'''
printf 'fixture-sentinel' > "$ACCOUNTS_DIR/example.credentials.enc"
if write_backup example credentials '[]'; then exit 98; fi
''')
            assert (fixture / 'home/.claude/accounts/example.credentials.enc').read_text() == 'fixture-sentinel'

            fixture, state = run(r'''
printf '%s' "$(cat "$FIXTURE/target.json")" | encrypt_to "$ACCOUNTS_DIR/legacy.credentials.enc"
printf '{"userID":"fixture-target-id"}' > "$ACCOUNTS_DIR/legacy.json"
load_account legacy
''')
            assert json.loads(state.read_text()) == {**BASE, 'claudeAiOauth': TARGET['claudeAiOauth']}
            assert json.loads((fixture / 'home/.claude.json').read_text())['mcpServers'] == {'fixture': {'type': 'http'}}

            fixture, state = run(r'''
save_account original
printf 'original\n' > "$ACTIVE_FILE"
new_command
''')
            assert json.loads(state.read_text()) == {k: v for k, v in BASE.items() if k != 'claudeAiOauth'}
            assert not (fixture / 'home/.claude/accounts/fresh.credentials.enc').exists()

            fixture, state = run(r'''
save_account original
printf 'original\n' > "$ACTIVE_FILE"
printf '%s' "$(cat "$FIXTURE/target.json")" | encrypt_to "$ACCOUNTS_DIR/other.credentials.enc"
printf '{"userID":"fixture-other-id"}' > "$ACCOUNTS_DIR/other.json"
set_timezone other UTC
set_timezone original UTC
use_command other
jq '.mcpOAuth.extra = {accessToken:"fixture-new-shared"}' "$STATE" > "$FIXTURE/refreshed.json"
cp "$FIXTURE/refreshed.json" "$STATE"
use_command original
''')
            expected = json.loads(json.dumps(BASE))
            expected['mcpOAuth']['extra'] = {'accessToken': 'fixture-new-shared'}
            assert json.loads(state.read_text()) == expected
    print(f'PASS: {count} isolated checks across mocked macOS Keychain and Linux files; '
          'legacy backups, logout/new, roundtrip with new MCP auth, corruption guards, and login-only encrypted backups.')


if __name__ == '__main__':
    main()
