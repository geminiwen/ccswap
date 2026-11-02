"""save/use ask before closing a running Claude Code; usage runs ccusage over every account."""
import os
import pty
import select
import time
import unittest

import test_ccacc_shell_integration as integration

# save/use read the weekly reset from Keychain and network; fixtures forbid both.
os.environ["CCACC_NO_WEEKLY_RESET"] = "1"
# list/current/use refresh the latest Release from GitHub; fixtures forbid the network.
os.environ["CCACC_NO_UPDATE_CHECK"] = "1"


# Only the process boundary is fake: the listed PIDs and the stop request.
PROCESS_MOCKS = r'''
source "$FIXTURE_CORE" help >/dev/null
claude_process_ids() { [ ! -f "$HOME/running" ] || printf '4242\n'; }
stop_claude() { printf 'stop %s\n' "$*" >> "$HOME/close-operations"; rm -f "$HOME/running"; }
'''


class ClosePromptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ShellIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        self.operations = self.home / "close-operations"

    def run_pipe(self, body, success, stdin=""):
        return self.fixture.run_process(["/bin/bash", "-c", PROCESS_MOCKS + body],
                                        success=success, stdin=stdin)

    def run_pty(self, body, answer, replies=()):
        pid, master = pty.fork()
        if pid == 0:
            os.execve("/bin/bash", ["bash", "--noprofile", "--norc", "-c", PROCESS_MOCKS + body],
                      self.fixture.env)
        data, sent, status = b"", False, None
        pending = list(replies)
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        data += os.read(master, 65536)
                    except OSError:
                        pass
                if pending and pending[0][0] in data:
                    os.write(master, pending.pop(0)[1])
                if not sent and not pending and b"(y/N) " in data:
                    os.write(master, answer)
                    sent = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = os.waitstatus_to_exitcode(result)
                    break
        finally:
            if status is None:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            os.close(master)
        self.assertFalse(self.fixture.forbidden.exists())
        return status, data.decode(errors="replace")

    def stops(self):
        return self.operations.read_text().splitlines() if self.operations.exists() else []

    def test_nothing_running_never_asks_or_stops(self):
        result = self.run_pipe("confirm_close_claude save", success=True)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.stops(), [])

    def test_running_without_terminal_refuses_without_stopping(self):
        (self.home / "running").touch()
        for command in ("save", "use"):
            result = self.run_pipe(f"confirm_close_claude {command}", success=False, stdin="y\n")
            self.assertIn("Claude Code is running", result.stderr)
            self.assertIn(f"'ccacc {command}' in a terminal", result.stderr)
            self.assertNotIn("(y/N)", result.stderr)
            self.assertEqual(self.stops(), [])

    def test_terminal_yes_closes_with_ancestor_guard(self):
        (self.home / "running").touch()
        status, output = self.run_pty("confirm_close_claude use", b"y\n")
        self.assertEqual(status, 0, output)
        self.assertIn("Claude Code is running. Close it now? (y/N)", output)
        self.assertEqual(self.stops(), ["stop --guard-ancestors"])

    def test_terminal_other_answers_cancel_without_stopping(self):
        for answer in (b"\n", b"n\n", b"no\n", b"\x04"):
            with self.subTest(answer=answer):
                (self.home / "running").touch()
                status, output = self.run_pty("confirm_close_claude save", answer)
                self.assertEqual(status, 1, output)
                self.assertIn("Cancelled; Claude Code left running", output)
                self.assertEqual(self.stops(), [])

    def test_save_and_use_cancel_before_any_change(self):
        self.fixture.cli("configure-shell")
        accounts = self.home / ".claude/accounts"
        (accounts / "tokyo.json").write_text("{}")
        (accounts / ".timezones").mkdir(exist_ok=True)
        (accounts / ".timezones/tokyo").write_text("Asia/Tokyo\n")
        (self.home / "running").touch()
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        for body in ("main save fresh --timezone UTC", "main use tokyo --no-yolo"):
            with self.subTest(body=body):
                status, output = self.run_pty(
                    "migrate_encrypted_backups() { printf 'password\\n' >> \"$HOME/close-operations\"; }\n"
                    + body, b"n\n")
                self.assertEqual(status, 1, output)
                self.assertIn("Close it now? (y/N)", output)
                self.assertEqual(self.stops(), [])
                after = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
                self.assertEqual(after, before)


    def test_new_cancels_before_any_change(self):
        # A logged-out unnamed session: new asks no backup question, only the
        # timezone, then whether to close Claude.
        (self.home / ".claude/accounts/.active").unlink(missing_ok=True)
        (self.home / ".claude.json").write_text("{}")
        (self.home / "running").touch()
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        status, output = self.run_pty("read_credentials() { printf '{}'; }\nmain new", b"n\n",
                                      replies=[(b"Choose timezone", b"\n")])
        self.assertEqual(status, 1, output)
        self.assertIn("Close it now? (y/N)", output)
        self.assertIn("Cancelled; Claude Code left running", output)
        self.assertEqual(self.stops(), [])
        self.assertEqual({p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}, before)


class UsageCommandTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ShellIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        self.accounts = self.home / ".claude/accounts"
        self.calls = self.fixture.fixture / "ccusage-calls"
        self.fixture.env["CCUSAGE_CALLS"] = str(self.calls)
        self.fixture.executable("ccusage", '#!/bin/bash\nprintf "%s|%s\\n" "$CLAUDE_CONFIG_DIR" "$*" '
                                '>> "$CCUSAGE_CALLS"\nexit 7\n')

    def test_usage_passes_live_and_saved_history_dirs_and_arguments(self):
        (self.home / ".claude/projects").mkdir(parents=True, exist_ok=True)
        for name in ("tokyo", "sydney", ".hidden", "bad name"):
            (self.accounts / name / "projects").mkdir(parents=True)
        (self.accounts / "empty").mkdir()
        (self.accounts / "linked").symlink_to(self.accounts / "tokyo", target_is_directory=True)
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        result = self.fixture.cli("usage", "daily", "--json", success=False)
        self.assertEqual(result.returncode, 7)
        dirs, args = self.calls.read_text().strip().split("|")
        claude = str(self.home / ".claude")
        self.assertEqual(dirs.split(","), [claude, f"{claude}/accounts/sydney", f"{claude}/accounts/tokyo"])
        self.assertEqual(args, "daily --json")
        self.assertEqual({p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}, before)

    def test_help_lists_usage_only_when_ccusage_is_installed(self):
        self.assertIn("usage [ccusage args]", self.fixture.cli("help").stdout)
        (self.fixture.bin / "ccusage").unlink()
        self.fixture.env["PATH"] = str(self.fixture.bin) + ":/usr/bin:/bin"
        self.assertNotIn("usage [ccusage args]", self.fixture.cli("help").stdout)

    def hide_surge(self):
        core = self.fixture.core
        core.write_text(core.read_text().replace(
            'SURGE_APP="/Applications/Surge.app"', f'SURGE_APP="{self.fixture.fixture}/NoSurge.app"', 1))
        (self.fixture.bin / "surge-cli").unlink()
        self.fixture.env["PATH"] = str(self.fixture.bin) + ":/usr/bin:/bin"

    def test_help_lists_surge_when_the_app_or_cli_is_installed_even_if_not_running(self):
        self.hide_surge()
        self.assertNotIn("surge <name>", self.fixture.cli("help").stdout)
        (self.fixture.fixture / "NoSurge.app").mkdir()
        self.assertIn("surge <name>", self.fixture.cli("help").stdout)
        (self.fixture.fixture / "NoSurge.app").rmdir()
        self.fixture.executable("surge-cli", "#!/bin/bash\nexit 1\n")
        self.assertIn("surge <name>", self.fixture.cli("help").stdout)

    def test_surge_command_refuses_unknown_account_and_missing_surge(self):
        self.hide_surge()
        result = self.fixture.cli("surge", "nobody", success=False)
        self.assertIn("Account 'nobody' not found", result.stderr)
        self.accounts.mkdir(parents=True, exist_ok=True)
        (self.accounts / "tokyo.json").write_text("{}")
        result = self.fixture.cli("surge", "tokyo", success=False)
        self.assertIn("Surge is not installed", result.stderr)
        self.fixture.executable("surge-cli", "#!/bin/bash\nexit 1\n")
        result = self.fixture.cli("surge", "tokyo", success=False)
        self.assertIn("Surge is not running", result.stderr)
        self.assertFalse((self.accounts / "tokyo.surge").exists())

    def test_missing_ccusage_explains_install(self):
        (self.fixture.bin / "ccusage").unlink()
        self.fixture.env["PATH"] = str(self.fixture.bin) + ":/usr/bin:/bin"
        result = self.fixture.cli("usage", success=False)
        self.assertIn("ccusage is not installed", result.stderr)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
