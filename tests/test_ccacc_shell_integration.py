"""Core + companion E2E in fixture HOME/startup files; no real account operations."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
JQ = shutil.which("jq")


class ShellIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.reset_fixture()

    def reset_fixture(self):
        (ROOT / ".orca").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".orca", prefix="shell-integration-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / "home"
        self.accounts = self.home / ".claude/accounts"
        self.accounts.mkdir(parents=True)
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        deployment = self.fixture / "installed tools"
        deployment.mkdir()
        self.core = deployment / "ccacc"
        shutil.copyfile(ROOT / "ccacc", self.core)
        shutil.copyfile(ROOT / "ccacc_shell.sh", deployment / "ccacc_shell.sh")
        # Intentionally non-executable: both configure-shell and the generated
        # runtime timezone query must work through bash.
        self.core.chmod(0o600)
        self.launches = self.fixture / "launches.jsonl"
        self.wrappers = self.fixture / "wrappers.jsonl"
        self.forbidden = self.fixture / "forbidden.log"
        self.argv = self.fixture / "jq-argv"
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.fixture),
                        SHELL="/bin/bash", PATH=str(self.bin) + ":" + os.environ["PATH"],
                        LAUNCHES=str(self.launches), WRAPPERS=str(self.wrappers),
                        FORBIDDEN=str(self.forbidden), JQ_ARGV_LOG=str(self.argv),
                        FIXTURE_CORE=str(self.core))
        for name in ("ZDOTDIR", "BASH_ENV", "ENV", "CLAUDE_CONFIG_DIR"):
            self.env.pop(name, None)
        for name in ("ps", "kill", "security", "osascript", "surge-cli", "openssl",
                     "curl", "wget", "open", "op"):
            self.executable(name, '#!/bin/bash\nprintf "%s\\n" "$0" >> "$FORBIDDEN"\nexit 98\n')
        self.executable("jq", '#!/bin/bash\nprintf "%s\\0" "$@" >> "$JQ_ARGV_LOG"\nexec '
                        + shlex.quote(JQ) + ' "$@"\n')
        self.executable("claude", r'''#!/bin/bash
command jq -cn --args '{tz:env.TZ, browser:env.BROWSER, config:env.CLAUDE_CONFIG_DIR,
    outer:env.OUTER, inner:env.INNER, args:$ARGS.positional}' -- "$@" >> "$LAUNCHES"
''')
        self.executable("_op_agent", r'''#!/bin/bash
command jq -cn --args '{outer:env.OUTER, browser:env.BROWSER, tz:env.TZ,
    args:$ARGS.positional}' -- "$@" >> "$WRAPPERS"
exec "$@"
''')
        (self.home / ".claude.json").write_text('{"userID":"fixture","mcpServers":{"shared":{}}}')
        self.account("sydney", "Australia/Sydney")
        self.account("tokyo", "Asia/Tokyo")
        (self.accounts / ".active").write_text("sydney\n")

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def account(self, name, zone):
        (self.accounts / (name + ".json")).write_text("{}")
        metadata = self.accounts / ".timezones"
        metadata.mkdir(exist_ok=True)
        (metadata / name).write_text(zone + "\n")

    def run_process(self, args, success=True, stdin=""):
        result = subprocess.run(args, env=self.env, input=stdin, text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, (args, result.stdout, result.stderr))
        self.assertFalse(self.forbidden.exists(),
                         self.forbidden.read_text() if self.forbidden.exists() else "")
        return result

    def cli(self, *args, success=True):
        return self.run_process(["/bin/bash", str(self.core), *args], success=success)

    def harness(self, body, success=True):
        # Source the actual core file so its BASH_SOURCE points to deployment.
        # Only credential/account movement and process/Surge work are replaced.
        mocks = r'''
source "$FIXTURE_CORE" help >/dev/null
ask_password() { printf 'password\n' >> "$HOME/operations"; }
save_account() { printf '{}\n' > "$ACCOUNTS_DIR/$1.json"; }
load_account() { printf 'load %s\n' "$1" >> "$HOME/operations"; }
backup_active() { :; }
stop_claude() { printf 'stop\n' >> "$HOME/operations"; }
note_device_keys() { :; }
stash_paths() { :; }
unstash_paths() { :; }
reset_account_state() { :; }
replace_credentials() { printf 'credentials\n' >> "$HOME/operations"; }
setup_surge() { :; }
apply_surge() { :; }
'''
        return self.run_process(["/bin/bash", "-c", mocks + "\n" + body], success=success)

    def startup(self, shell, definition=None, zdotdir=False):
        self.env["SHELL"] = "/bin/" + shell
        startup = self.home
        if zdotdir:
            startup = self.home / "zsh settings"
            startup.mkdir()
            # This assignment is deliberately NOT exported by .zshenv.
            (self.home / ".zshenv").write_text("ZDOTDIR=" + shlex.quote(str(startup)) + "\n")
        content = "export PATH=" + shlex.quote(self.env["PATH"]) + "\n"
        content += "printf 'fixture startup chatter\\n'\n"
        if definition is not None:
            content += "builtin alias " + shlex.quote("claude=" + definition) + "\n"
        rc = startup / (".bashrc" if shell == "bash" else ".zshrc")
        rc.write_text(content)
        if shell == "bash":
            login = self.home / ".bash_profile"
            login.write_text('. "$HOME/.bashrc"\n')
        else:
            login = startup / ".zlogin"
            login.write_text(content)
        return startup, (rc, login)

    def probe(self, alias="claude"):
        result = self.run_process(["/bin/bash", "-c",
                                  'source "$FIXTURE_CORE" help >/dev/null\nprobe_alias "$1"',
                                  "fixture", alias])
        return json.loads(result.stdout)

    def interactive(self, shell, code, startup=True):
        args = ["/bin/" + shell]
        if not startup:
            args += ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
        # Feed separate input lines like a terminal. In particular, Zsh parses a
        # whole -c string before a newly sourced alias becomes available.
        args += ["-il" if startup else "-i"]
        return self.run_process(args, stdin=code)

    def records(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_real_startup_wrapper_dynamic_timezone_and_repeat_install(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                # Give each shell its own complete HOME fixture.
                if shell == "zsh":
                    self.reset_fixture()
                definition = ("OUTER='outer value' _op_agent env INNER='inside value' "
                              "BROWSER=firefox TZ=UTC CLAUDE_CONFIG_DIR='/fixture config' "
                              "claude --dangerously-skip-permissions --model 'model name'")
                _, rcs = self.startup(shell, definition, zdotdir=shell == "zsh")
                self.assertEqual(self.probe()["definition"], definition)
                result = self.cli("configure-shell")
                self.assertIn("sydney (Australia/Sydney)", result.stdout)
                self.assertIn("In your current shell, run: source", result.stdout)
                self.assertIn("New interactive terminals", result.stdout)
                self.assertFalse(self.launches.exists())
                self.assertFalse(self.wrappers.exists())
                original = json.loads((self.home / ".ccacc-shell/original-alias.json").read_text())
                self.assertEqual(original["definition"], definition)
                snapshot = {rc: (rc.read_bytes(), rc.stat().st_mtime_ns) for rc in rcs}
                self.cli("configure-shell", "sydney")
                self.assertEqual({rc: (rc.read_bytes(), rc.stat().st_mtime_ns) for rc in rcs}, snapshot)
                self.interactive(shell, ":")
                self.assertFalse(self.launches.exists())
                # One source, two launches, with an active-account change between.
                hook = shlex.quote(str(self.home / ".ccacc-shell/hook.sh"))
                self.interactive(shell, f". {hook}\nclaude 'user argument'\n"
                                 "printf 'tokyo\\n' > \"$HOME/.claude/accounts/.active\"\n"
                                 "claude 'next argument'\n", startup=False)
                launches = self.records(self.launches)
                self.assertEqual([entry["tz"] for entry in launches], ["Australia/Sydney", "Asia/Tokyo"])
                for index, entry in enumerate(launches):
                    self.assertEqual(entry["browser"], "false")
                    self.assertEqual(entry["config"], "/fixture config")
                    self.assertEqual(entry["outer"], "outer value")
                    self.assertEqual(entry["inner"], "inside value")
                    self.assertEqual(entry["args"], ["--dangerously-skip-permissions", "--model",
                                                     "model name", ("user argument", "next argument")[index]])
                wrappers = self.records(self.wrappers)
                self.assertEqual(len(wrappers), 2)
                self.assertEqual([entry["tz"] for entry in wrappers], ["Australia/Sydney", "Asia/Tokyo"])
                self.assertTrue(all(entry["outer"] == "outer value" and entry["browser"] == "false"
                                    and entry["args"][0] == "env" for entry in wrappers))
                self.interactive(shell, "claude 'fresh terminal'\n")
                self.assertEqual(self.records(self.launches)[-1]["tz"], "Asia/Tokyo")

    def test_nonexported_zdotdir_metadata_for_existing_and_missing_alias(self):
        startup, rcs = self.startup("zsh", "TZ=UTC claude", zdotdir=True)
        self.assertNotIn("ZDOTDIR", self.env)
        self.assertEqual(self.probe(), {"definition": "TZ=UTC claude", "startupDirectory": str(startup)})
        self.assertEqual(self.probe("missing"), {"missing": True, "startupDirectory": str(startup)})
        self.cli("configure-shell")
        for rc in rcs:
            self.assertIn("# >>> ccacc shell hook >>>", rc.read_text())
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".zlogin").exists())

    def test_missing_alias_installs_default_without_source_time_launch(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                if shell == "zsh":
                    self.reset_fixture()
                startup, _ = self.startup(shell, zdotdir=shell == "zsh")
                self.assertEqual(self.probe(), {"missing": True, "startupDirectory": str(startup)})
                self.cli("configure-shell", "tokyo")
                self.assertFalse((self.home / ".ccacc-shell/original-alias.json").exists())
                self.interactive(shell, ":")
                self.assertFalse(self.launches.exists())
                self.interactive(shell, "claude 'plain argument'\n")
                self.assertEqual(self.records(self.launches)[0]["browser"], "false")
                # Installing for another account never changes the active account.
                self.assertEqual(self.records(self.launches)[0]["tz"], "Australia/Sydney")

    def test_runtime_hook_never_prompts_or_uses_legacy_alias_timezone(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                if shell == "zsh":
                    self.reset_fixture()
                self.startup(shell, "TZ=Asia/Tokyo claude", zdotdir=shell == "zsh")
                self.cli("configure-shell")
                (self.accounts / ".timezones/sydney").unlink()
                aliases = self.accounts / ".aliases"
                aliases.mkdir()
                (aliases / "sydney.json").write_text(json.dumps({"env": {"TZ": "Asia/Tokyo"}}))
                before = {path.relative_to(self.home): path.read_bytes()
                          for path in self.home.rglob("*") if path.is_file()}
                hook = shlex.quote(str(self.home / ".ccacc-shell/hook.sh"))
                result = self.interactive(shell, f". {hook}\nclaude\nexit 0\n", startup=False)
                self.assertIn("cannot read the active account timezone", result.stderr)
                self.assertNotIn("Choose timezone", result.stderr)
                self.assertFalse(self.launches.exists())
                self.assertEqual(before, {path.relative_to(self.home): path.read_bytes()
                                          for path in self.home.rglob("*") if path.is_file()})

    def test_save_new_use_and_same_account_use_install_hook(self):
        self.startup("bash", "TZ=UTC claude --model fixture")
        result = self.harness("main save sydney --timezone Australia/Sydney")
        self.assertIn("Claude shell hook installed", result.stdout)
        original = (self.home / ".ccacc-shell/original-alias.json").read_bytes()
        hook = self.home / ".ccacc-shell/hook.sh"
        hook.unlink()  # Show the same-account fast path actually repairs setup.
        result = self.harness("main use sydney")
        self.assertIn("already active", result.stdout)
        self.assertIn("Claude shell hook installed", result.stdout)
        self.assertTrue(hook.exists())
        self.harness("main use tokyo")
        self.assertEqual((self.accounts / ".active").read_text(), "tokyo\n")
        self.harness("main new fresh --timezone Australia/Sydney")
        self.assertEqual(self.cli("timezone", "fresh").stdout, "Australia/Sydney\n")
        self.assertEqual((self.accounts / ".active").read_text(), "fresh\n")
        self.assertEqual((self.home / ".ccacc-shell/original-alias.json").read_bytes(), original)
        self.assertFalse(self.launches.exists())

    def test_missing_library_and_unsupported_shell_fail_before_account_operations(self):
        self.startup("bash", "TZ=UTC claude")
        library = self.core.with_name("ccacc_shell.sh")
        library.rename(library.with_suffix(".saved"))
        self.assertIn("ccacc_shell.sh companion",
                      self.cli("configure-shell", "sydney", success=False).stderr)
        for command in ("save next --timezone UTC", "new next --timezone UTC", "use tokyo"):
            result = self.harness("main " + command, success=False)
            self.assertIn("ccacc_shell.sh companion", result.stderr)
            self.assertNotIn("shell hook installed", result.stdout)
            self.assertFalse((self.home / "operations").exists())
        self.env["SHELL"] = "/bin/fish"
        result = self.harness("main use tokyo", success=False)
        self.assertIn("only Bash and Zsh", result.stderr)
        self.assertFalse((self.home / "operations").exists())
        self.assertEqual((self.accounts / ".active").read_text(), "sydney\n")

    def test_configure_shell_needs_metadata_only_and_preserves_credentials(self):
        self.startup("bash", "TZ=UTC claude")
        (self.home / ".claude.json").unlink()
        credentials = self.home / ".claude/.credentials.json"
        credentials.write_text("fixture credential sentinel, deliberately invalid JSON")
        before = credentials.read_bytes(), credentials.stat().st_mtime_ns
        self.cli("configure-shell")
        self.assertEqual((credentials.read_bytes(), credentials.stat().st_mtime_ns), before)
        self.assertFalse(self.launches.exists())

    def home_snapshot(self):
        return {path.relative_to(self.home): (path.read_bytes(), path.stat().st_mtime_ns,
                                             path.stat().st_mode & 0o777)
                for path in self.home.rglob("*") if path.is_file()}

    def test_full_preflight_is_read_only_and_blocks_unsupported_alias_before_switch(self):
        self.startup("bash", "TZ=UTC claude --model fixture")
        before = self.home_snapshot()
        self.harness("configure_shell_preflight tokyo Asia/Tokyo")
        self.assertEqual(self.home_snapshot(), before)
        self.assertFalse((self.home / ".ccacc-shell").exists())
        # The original body is data even when it contains command substitution.
        (self.home / ".bashrc").write_text(
            "builtin alias " + shlex.quote('claude=TZ="$(touch should-not-run)" claude') + "\n")
        credentials = self.home / ".claude/.credentials.json"
        credentials.write_text("fixture credential sentinel")
        before = self.home_snapshot()
        for command in ("use tokyo", "save next --timezone UTC", "new next --timezone UTC"):
            result = self.harness("main " + command, success=False)
            self.assertIn("unsupported Claude alias", result.stderr)
            self.assertIn("preflight failed; account unchanged", result.stderr)
            self.assertEqual(self.home_snapshot(), before)
            self.assertFalse((self.home / "operations").exists())
            self.assertFalse((self.home / ".ccacc-shell").exists())
        self.cli("configure-shell", "tokyo", success=False)
        self.assertEqual(self.home_snapshot(), before)

    def test_configure_shell_rejects_missing_or_invalid_tz_without_credentials(self):
        self.startup("bash")
        for name in ("missing", "sydney"):
            if name == "sydney":
                (self.accounts / ".timezones/sydney").write_text("Invalid/Zone\n")
            result = self.cli("configure-shell", name, success=False)
            self.assertIn("No valid saved timezone", result.stderr)
            self.assertFalse((self.home / ".ccacc-shell").exists())
        self.cli("configure-shell", "sydney", "extra", success=False)

    def test_alias_content_stays_out_of_external_jq_arguments(self):
        secret = "fixture-secret-never-an-argv-value"
        definition = f"TOKEN={secret} TZ=Asia/Tokyo claude --model fixture"
        self.startup("zsh", definition, zdotdir=True)
        self.assertEqual(self.probe()["definition"], definition)
        # Exercise raw alias backup through real core paths with an explicit timezone.
        self.harness("main save captured --alias claude --timezone UTC")
        backup = json.loads((self.accounts / ".aliases/captured.json").read_text())
        self.assertEqual(backup["definition"], definition)
        self.assertNotIn("env", backup)
        self.assertEqual(self.cli("timezone", "captured").stdout, "UTC\n")
        self.assertNotIn(secret.encode(), self.argv.read_bytes())
        self.assertFalse(self.launches.exists())


if __name__ == "__main__":
    unittest.main()
