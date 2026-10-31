"""Fixture-only CLI and probe checks; run with uv run tests/test_ccacc_alias_timezone.py."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "ccacc").read_text().rsplit('main "$@"', 1)[0]


class AliasTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".orca", prefix="alias-timezone-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name)
        self.home = self.fixture / "home"
        self.accounts = self.home / ".claude/accounts"
        self.accounts.mkdir(parents=True)
        self.alias_metadata = self.accounts / ".aliases"
        self.alias_metadata.mkdir()
        self.timezones = self.accounts / ".timezones"
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        self.log = self.fixture / "forbidden.log"
        self.probes = self.fixture / "probes.log"
        # Fail closed if any metadata operation reaches process control, Keychain,
        # Surge or encryption. No real account/config directory is read or changed.
        for name in ("ps", "kill", "security", "osascript", "surge-cli", "openssl"):
            self.executable(name, '#!/bin/bash\nprintf "%s\\n" "$0" >> "$FORBIDDEN"\nexit 98\n')
        for shell in ("bash", "zsh"):
            # Execute the actual probe code with startup disabled and fixture aliases.
            # The stub itself never launches an interactive/login shell.
            self.executable(shell, f'''#!/bin/bash
printf '%s\\n' "$*" >> "$PROBES"
[ "$1" = -ilc ] || exit 97
probe_code="$2"
shift 2
exec /bin/{shell} {'--noprofile --norc' if shell == 'bash' else '-f'} -c '. "$ALIAS_FIXTURE"; '"$probe_code" "$@"
''')
        self.aliases = self.fixture / "aliases.sh"
        self.aliases.write_text("printf 'startup noise\\n'\ntrap \"printf 'logout noise\\n'\" EXIT\n")
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.fixture),
                        SHELL=str(self.bin / "bash"), PATH=str(self.bin) + ":" + os.environ["PATH"],
                        ALIAS_FIXTURE=str(self.aliases), FORBIDDEN=str(self.log),
                        PROBES=str(self.probes), CLAUDE_CONFIG_DIR="fixture-ignored")
        # Avoid inherited startup hooks while invoking bash -c in harness tests.
        self.env.pop("BASH_ENV", None)
        self.env.pop("ENV", None)

    def executable(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o700)

    def define(self, name, definition):
        with self.aliases.open("a") as handle:
            handle.write("builtin alias " + shlex.quote(name + "=" + definition) + "\n")

    def run_cli(self, *args, success=True, stdin=""):
        result = subprocess.run(["/bin/bash", str(ROOT / "ccacc"), *args],
                                env=self.env, text=True, input=stdin, capture_output=True)
        self.assertEqual(result.returncode == 0, success, (args, result.stdout, result.stderr))
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")
        return result

    def run_harness(self, body, stdin="", success=True):
        mocks = '''
ask_password() { MASTER_PASSWORD=fixture-only; }
require_shell_hook() { :; }
save_account() { printf '{"userID":"fixture-id"}' > "$ACCOUNTS_DIR/$1.json"; }
setup_surge() { :; }
apply_surge() { :; }
kill() { printf 'kill\\n' >> "$FORBIDDEN"; return 98; }
'''
        result = subprocess.run(["/bin/bash", "-c", SOURCE + mocks + body], env=self.env,
                                input=stdin, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        self.assertFalse(self.log.exists())
        return result

    def metadata_path(self, name="example"):
        return self.alias_metadata / (name + ".json")

    def metadata(self, name="example"):
        return json.loads(self.metadata_path(name).read_text())

    def test_bash_and_zsh_capture_exact_definition_without_execution(self):
        self.alias_metadata.rmdir()
        definition = ('TZ="Asia/Tokyo" CLAUDE_CONFIG_DIR="$HOME/fixture dir" '
                      'claude "don\'t change" "$(touch \'$HOME/executed\')"; '
                      'touch "$HOME/executed"\n')
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                self.env["SHELL"] = str(self.bin / shell)
                self.define("quoted-alias", definition)
                self.run_cli("alias", "example", "quoted-alias")
                self.assertEqual(self.metadata(), {"aliasName": "quoted-alias", "definition": definition})
                self.assertEqual((self.alias_metadata / "example.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual(self.alias_metadata.stat().st_mode & 0o777, 0o700)
                self.assertFalse((self.home / "executed").exists())

    def test_missing_alias_does_not_create_or_delete_backup(self):
        for shell in ("bash", "zsh"):
            self.env["SHELL"] = str(self.bin / shell)
            self.run_cli("alias", "missing")
            self.assertFalse((self.alias_metadata / "missing.json").exists())
            backup = self.alias_metadata / "example.json"
            backup.write_text("fixture-unchanged")
            backup.chmod(0o640)
            self.run_cli("alias", "example", "missing")
            self.assertEqual(backup.read_text(), "fixture-unchanged")
            self.assertEqual(backup.stat().st_mode & 0o777, 0o640)

    def test_alias_definitions_never_supply_runtime_timezone(self):
        for definition in ("TZ=UTC claude", "TZ='Australia/Sydney' claude",
                           'env TZ="Asia/Tokyo" claude',
                           '_op_agent env BROWSER=false TZ=Asia/Tokyo claude',
                           'TZ="$(touch fixture-executed)" claude'):
            with self.subTest(definition=definition):
                self.define("example", definition)
                self.run_cli("alias", "example")
                self.assertEqual(self.metadata(), {"aliasName": "example", "definition": definition})
                self.assertEqual(self.run_cli("timezone", "example", success=False).stdout, "")
                self.assertFalse((ROOT / "fixture-executed").exists())

    def test_timezone_active_lookup_and_no_config_or_password_needed(self):
        self.define("example", "TZ=UTC claude")
        self.run_cli("alias", "example")
        self.run_cli("timezone", success=False)
        self.run_cli("timezone", "example", "UTC")
        (self.accounts / "example.json").write_text("{}")
        (self.accounts / ".active").write_text("example\n")
        self.assertEqual(self.run_cli("timezone").stdout, "UTC\n")
        self.probes.unlink()
        self.assertEqual(self.run_cli("timezone", "example").stdout, "UTC\n")
        self.assertFalse(self.probes.exists())
        (self.accounts / ".active").write_text("../outside\n")
        self.assertEqual(self.run_cli("timezone", success=False).stdout, "")

    def test_dynamic_alias_preserves_saved_timezone_without_execution(self):
        (self.home / ".claude.json").write_text("{}")
        self.define("emocode", "TZ=Australia/Sydney claude")
        self.run_cli("alias", "emocode")
        self.run_cli("timezone", "emocode", "Australia/Sydney")
        dynamic = 'TZ="$(_claude_timezone)" claude "$HOME/fixture"'
        with self.aliases.open("a") as handle:
            handle.write('_claude_timezone() { touch "$HOME/executed"; printf UTC; }\n')
        self.define("claude", dynamic)
        self.define("dynamic", dynamic + " --verbose")
        self.run_cli("alias", "emocode", "claude")
        self.assertEqual(self.metadata("emocode"), {
            "aliasName": "claude", "definition": dynamic})
        self.run_harness('main save emocode --alias dynamic')
        self.assertEqual(self.metadata("emocode"), {
            "aliasName": "dynamic", "definition": dynamic + " --verbose"})
        self.assertEqual(self.run_cli("timezone", "emocode").stdout, "Australia/Sydney\n")
        self.assertFalse((self.home / "executed").exists())

    def test_use_roundtrip_keeps_alias_metadata_separate_from_dotted_account_stash(self):
        (self.home / ".claude.json").write_text("{}")
        for name, zone in (("foo", "UTC"), ("foo.alias", "Australia/Sydney")):
            (self.accounts / (name + ".json")).write_text("{}")
            self.define(name, "TZ=" + zone + " claude")
            self.run_cli("alias", name)
            self.run_cli("timezone", name, zone)
        history = self.home / ".claude/history.jsonl"
        history.write_text("foo history\n")
        (self.accounts / ".active").write_text("foo\n")
        self.run_harness('''
stop_claude() { :; }
note_device_keys() { :; }
load_account() { :; }
main use foo.alias
''')
        self.assertEqual((self.accounts / "foo/history.jsonl").read_text(), "foo history\n")
        self.assertFalse(history.exists())
        history.write_text("foo.alias history\n")
        self.run_harness('''
stop_claude() { :; }
note_device_keys() { :; }
load_account() { :; }
main use foo
''')
        self.assertEqual(history.read_text(), "foo history\n")
        self.assertEqual((self.accounts / "foo.alias/history.jsonl").read_text(), "foo.alias history\n")
        self.assertNotIn("env", self.metadata("foo"))
        self.assertNotIn("env", self.metadata("foo.alias"))
        self.run_cli("rm", "foo", stdin="y\n")
        self.assertFalse(self.metadata_path("foo").exists())
        self.assertTrue((self.accounts / "foo.alias.json").exists())
        self.assertEqual((self.accounts / "foo.alias/history.jsonl").read_text(), "foo.alias history\n")
        self.assertNotIn("env", self.metadata("foo.alias"))

    def test_timezone_rejects_corrupt_or_invalid_metadata_without_stdout(self):
        self.define("example", 'TZ="$(_claude_timezone)" claude')
        for content in ('{bad', 'null', '[]', '{"env":{"TZ":23}}',
                        '{"env":{"TZ":"UTC\\n"}}', '{"env":{"TZ":"Invalid/Zone"}}',
                        '{"env":{"TZ":"../UTC"}}', '{"env":{"TZ":"$(touch nope)"}}',
                        '{"env":{"TZ":"UTC"}} {"env":{"TZ":"UTC"}}'):
            (self.alias_metadata / "example.json").write_text(content)
            self.assertEqual(self.run_cli("timezone", "example", success=False).stdout, "")
            self.run_cli("alias", "example")
            self.assertNotIn("env", self.metadata())

    def test_readonly_lookup_ignores_valid_legacy_timezone_without_prompt_or_writes(self):
        self.env["TZ"] = "Australia/Sydney"
        self.metadata_path().write_text(json.dumps({"env": {"TZ": "Asia/Tokyo"}}))
        (self.accounts / "example.json").write_text("{}")
        (self.accounts / ".active").write_text("example\n")
        self.timezones.mkdir()
        for saved in (None, "Invalid/Zone\n", "UTC\nUTC\n"):
            with self.subTest(saved=saved):
                if saved is not None:
                    (self.timezones / "example").write_text(saved)
                before = {path.relative_to(self.home): path.read_bytes()
                          for path in self.home.rglob("*") if path.is_file()}
                for args in (("timezone",), ("timezone", "example")):
                    result = self.run_cli(*args, success=False, stdin="1\n")
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "")
                self.assertFalse(self.probes.exists())
                self.assertEqual(before, {path.relative_to(self.home): path.read_bytes()
                                          for path in self.home.rglob("*") if path.is_file()})

    def test_invalid_names_never_probe_or_write(self):
        for name in (".aliases", "../escape", "-flag", "bad/name", "$(touch nope)", "has space", ""):
            self.run_cli("alias", name, success=False)
            self.run_cli("alias", "example", name, success=False)
            self.assertEqual(self.run_cli("timezone", name, success=False).stdout, "")
        self.assertFalse(self.probes.exists())
        self.assertEqual(list(self.accounts.iterdir()), [self.alias_metadata])
        self.assertEqual(list(self.alias_metadata.iterdir()), [])

    def test_metadata_is_not_an_account_and_dotted_names_remain_valid(self):
        (self.home / ".claude.json").write_text("{}")
        self.define("example", "TZ=UTC claude")
        self.run_cli("alias", "example")
        self.assertIn("No accounts saved", self.run_cli("ls").stdout)
        (self.accounts / ".active").write_text("example\n")
        self.assertIn("(not saved)", self.run_cli("current").stdout)
        self.run_cli("rm", "example", success=False, stdin="y\n")
        self.assertTrue((self.alias_metadata / "example.json").exists())
        (self.accounts / "example.alias.json").write_text("{}")
        self.run_cli("alias", "example.alias", "example")
        self.run_cli("timezone", "example.alias", "UTC")
        (self.accounts / ".active").write_text("example.alias\n")
        self.assertEqual(self.run_cli("timezone").stdout, "UTC\n")
        self.assertIn("example.alias", self.run_cli("ls").stdout)
        self.assertIn("example.alias", self.run_cli("current").stdout)

    def test_removal_cleans_only_target_metadata(self):
        (self.home / ".claude.json").write_text("{}")
        for name in ("example", "other"):
            self.run_cli("timezone", name, "UTC")
            (self.accounts / (name + ".json")).write_text("{}")
            (self.alias_metadata / (name + ".json")).write_text("fixture-metadata")
            (self.accounts / (name + ".surge")).write_text("{}")
            (self.accounts / (name + ".credentials.enc")).write_text("fixture-encrypted")
            (self.accounts / name).mkdir()
        (self.accounts / ".active").write_text("example\n")
        self.run_cli("rm", "example", stdin="y\n")
        self.assertEqual(sorted(path.name for path in self.accounts.iterdir()),
                         [".aliases", ".timezones", "other", "other.credentials.enc", "other.json", "other.surge"])
        self.assertEqual(sorted(path.name for path in self.alias_metadata.iterdir()), ["other.json"])
        self.assertEqual(sorted(path.name for path in self.timezones.iterdir()), ["other"])

    def test_save_default_explicit_alias_and_argument_compatibility(self):
        (self.home / ".claude.json").write_text("{}")
        self.define("example", "TZ=UTC claude")
        self.define("wrapper", "TZ=Asia/Tokyo claude")
        self.run_harness('main save example', stdin="1\n")
        self.assertNotIn("env", self.metadata())
        self.run_harness('main save example --alias wrapper')
        self.assertEqual(self.metadata()["aliasName"], "wrapper")
        self.assertNotIn("env", self.metadata())
        self.assertEqual(self.run_cli("timezone", "example").stdout, "UTC\n")
        self.run_harness('main save example --timezone Australia/Sydney --alias wrapper')
        self.assertEqual(self.run_cli("timezone", "example").stdout, "Australia/Sydney\n")
        self.run_harness('main save example --alias missing')
        self.assertEqual(self.metadata()["aliasName"], "wrapper")
        self.run_harness('main save missing', stdin="8\nEurope/London\n")
        self.assertFalse((self.alias_metadata / "missing.json").exists())
        self.assertEqual(self.run_cli("timezone", "missing").stdout, "Europe/London\n")
        self.run_harness('main save example --alias', success=False)
        self.run_harness('main save example --alias ""', success=False)
        self.run_harness('main save', success=False)

    def test_first_save_requires_numbered_or_custom_choice_despite_alias_or_environment(self):
        (self.home / ".claude.json").write_text("{}")
        self.env["TZ"] = "Australia/Sydney"
        self.define("Asia.Tokyo", "TZ=Australia/Sydney claude")
        self.metadata_path("Asia.Tokyo").write_text(json.dumps({"env": {"TZ": "Asia/Tokyo"}}))
        result = self.run_harness('main save Asia.Tokyo', stdin="1\n")
        self.assertIn("1) UTC", result.stderr)
        self.assertIn("8) Custom IANA timezone", result.stderr)
        self.assertEqual((self.timezones / "Asia.Tokyo").read_text(), "UTC\n")
        self.assertNotIn("env", self.metadata("Asia.Tokyo"))
        self.run_harness('main save absent', stdin="8\nEurope/Paris\n")
        self.assertEqual(self.run_cli("timezone", "absent").stdout, "Europe/Paris\n")
        self.assertFalse(self.metadata_path("absent").exists())
        self.assertEqual((self.timezones / "absent").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.timezones.stat().st_mode & 0o777, 0o700)

    def test_explicit_and_saved_choices_never_prompt(self):
        (self.home / ".claude.json").write_text("{}")
        result = self.run_harness('main save absent --timezone Australia/Sydney')
        self.assertNotIn("Choose timezone", result.stderr)
        result = self.run_harness('main save absent')
        self.assertNotIn("Choose timezone", result.stderr)
        self.assertEqual(self.run_cli("timezone", "absent").stdout, "Australia/Sydney\n")
        result = self.run_harness('main save absent --timezone Asia/Tokyo')
        self.assertNotIn("Choose timezone", result.stderr)
        self.assertEqual(self.run_cli("timezone", "absent").stdout, "Asia/Tokyo\n")

    def test_missing_timezone_and_bad_flags_fail_before_any_account_mutation(self):
        (self.home / ".claude.json").write_text('{"userID":"unchanged"}')
        (self.home / ".claude/.credentials.json").write_text("credentials unchanged")
        (self.accounts / ".active").write_text("original\n")
        (self.accounts / "original.json").write_text("{}")
        self.define("target", "TZ=Asia/Tokyo claude")
        self.metadata_path("target").write_text(json.dumps({"env": {"TZ": "Australia/Sydney"}}))
        before = {path.relative_to(self.home): path.read_bytes()
                  for path in self.home.rglob("*") if path.is_file()}
        forbidden = '''
ask_password() { printf 'password\\n' >> "$FORBIDDEN"; return 98; }
stop_claude() { printf 'process\\n' >> "$FORBIDDEN"; return 98; }
replace_credentials() { printf 'credentials\\n' >> "$FORBIDDEN"; return 98; }
save_account() { printf 'save\\n' >> "$FORBIDDEN"; return 98; }
'''
        for command in ('save target', 'new target', 'save target --timezone',
                        'save target --timezone Invalid/Zone', 'save target --unknown UTC',
                        'save target --alias wrapper --alias second',
                        'save target --timezone UTC --timezone UTC',
                        'new target --timezone ""', 'new target extra'):
            with self.subTest(command=command):
                for stdin in ("", "\n", "0\n", "9\n", "UTC\n", "1+1\n", "8\n", "8\n\n",
                              "8\nInvalid/Zone\n1\n") if command in ('save target', 'new target') else ("",):
                    self.run_harness(forbidden + "main " + command, stdin=stdin, success=False)
                    after = {path.relative_to(self.home): path.read_bytes()
                             for path in self.home.rglob("*") if path.is_file()}
                    self.assertEqual(after, before)
                    self.assertFalse(self.timezones.exists())
                    self.assertFalse(self.probes.exists())
        # Invalid option lists are rejected before even probing fixture aliases.
        self.probes.unlink(missing_ok=True)
        self.run_harness(forbidden + 'main save target --timezone UTC --alias', success=False)
        self.assertFalse(self.probes.exists())

    def test_timezone_setter_is_independent_and_query_prefers_valid_selection(self):
        self.run_cli("timezone", "example", "Australia/Sydney")
        self.assertFalse(self.probes.exists())
        self.assertFalse(self.metadata_path().exists())
        self.assertEqual(self.run_cli("timezone", "example").stdout, "Australia/Sydney\n")
        before = (self.timezones / "example").read_bytes()
        for zone in ("", "Invalid/Zone", "../UTC", "UTC\n", "$(touch nope)"):
            self.run_cli("timezone", "example", zone, success=False)
            self.assertEqual((self.timezones / "example").read_bytes(), before)
        self.run_cli("timezone", ".timezones", "UTC", success=False)
        self.define("example", "TZ=Asia/Tokyo claude")
        self.run_cli("alias", "example")
        self.assertEqual(self.run_cli("timezone", "example").stdout, "Australia/Sydney\n")
        (self.timezones / "example").write_text("UTC\nUTC\n")
        self.assertEqual(self.run_cli("timezone", "example", success=False).stdout, "")
        (self.timezones / "example").write_text("UTC\n\n")
        self.metadata_path().unlink()
        self.assertEqual(self.run_cli("timezone", "example", success=False).stdout, "")

    def test_new_obtains_timezone_before_clearing_credentials(self):
        (self.home / ".claude.json").write_text("{}")
        (self.accounts / "original.json").write_text("{}")
        (self.accounts / ".active").write_text("original\n")
        self.run_harness('''
stop_claude() { :; }
note_device_keys() { :; }
reset_account_state() { :; }
replace_credentials() {
    [ "$(timezone_command fresh)" = Australia/Sydney ] || return 97
    printf 'cleared' > "$HOME/credential-change"
}
main new fresh --timezone Australia/Sydney
''')
        self.assertEqual((self.home / "credential-change").read_text(), "cleared")
        self.assertEqual((self.accounts / ".active").read_text(), "fresh\n")
        self.assertFalse(self.metadata_path("fresh").exists())
        self.run_harness('''
stop_claude() { :; }
note_device_keys() { :; }
reset_account_state() { :; }
replace_credentials() { [ "$(timezone_command prompted)" = Europe/London ]; }
main new prompted
''', stdin="8\nEurope/London\n")
        self.assertEqual(self.run_cli("timezone", "prompted").stdout, "Europe/London\n")

    def test_probe_failure_preserves_backup(self):
        (self.alias_metadata / "example.json").write_text("fixture-unchanged")
        self.executable("bash", "#!/bin/bash\nprintf 'startup only\\n'\nexit 2\n")
        self.run_cli("alias", "example")
        self.assertEqual((self.alias_metadata / "example.json").read_text(), "fixture-unchanged")

    def test_aliases_named_command_and_builtin_are_never_invoked(self):
        definition = 'touch "$HOME/executed"'
        for shell in ("bash", "zsh"):
            self.env["SHELL"] = str(self.bin / shell)
            self.aliases.write_text("\\builtin alias " + shlex.quote("command=" + definition)
                                    + "\n\\builtin alias " + shlex.quote("builtin=" + definition) + "\n")
            for name in ("command", "builtin"):
                self.run_cli("alias", "example", name)
                self.assertEqual(self.metadata()["definition"], definition)
                self.assertFalse((self.home / "executed").exists())

    def test_process_matching_uses_owner_and_executable(self):
        result = self.run_harness(r'''
id() { printf '501\n'; }
ps() { cat <<'PROCESSES'
101 501 claude
102 501 /opt/bin/claude
103 501 /Users/fixture/.local/share/claude/versions/1.2.3
104 502 /opt/bin/claude
105 501 /Applications/Claude.app/Contents/MacOS/Claude
106 501 /opt/bin/other --mention /opt/bin/claude ignored
107 501 /opt/bin/claude-helper
PROCESSES
}
claude_process_ids
''')
        self.assertEqual(result.stdout, "101\n102\n103\n")

    def test_process_shutdown_escalates_with_fake_kill_only(self):
        result = self.run_harness(r'''
printf '0' > "$HOME/inspections"
claude_process_ids() {
    local n=$(cat "$HOME/inspections")
    printf '%s' "$((n+1))" > "$HOME/inspections"
    if [ "$n" -lt 7 ]; then printf 'fixture-pid\n'; fi
}
kill() { printf '%s\n' "$*" >> "$HOME/signals"; }
sleep() { :; }
stop_claude
''')
        self.assertIn("Stopping", result.stdout)
        self.assertEqual((self.home / "signals").read_text(),
                         "-TERM fixture-pid\n-KILL fixture-pid\n")
        self.run_harness('claude_process_ids() { return 1; }; stop_claude', success=False)


if __name__ == "__main__":
    (ROOT / ".orca").mkdir(exist_ok=True)
    unittest.main()
