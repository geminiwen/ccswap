"""Picker PTY regressions with isolated HOME and fake credential/process IO."""
import fcntl
import json
import os
import pty
import select
import shlex
import signal
import struct
import termios
import time
import unittest

import test_ccacc_use_confirmation as confirmation


class ProfilePickerTests(unittest.TestCase):
    def setUp(self):
        self.case = confirmation.UseConfirmationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture = self.case.fixture
        self.accounts = self.case.accounts
        (self.accounts / "sydney.json").write_text(json.dumps({"userID": "old-saved-id",
            "oauthAccount": {"emailAddress": "saved-sydney@example.test"}}))
        (self.accounts / "tokyo.json").write_text(json.dumps({"userID": "target-id",
            "oauthAccount": {"emailAddress": "saved-tokyo@example.test"}}))

    def run_pty(self, args=(), keys=b"q", backup=None, yolo=None, body=None,
                before_select=None, before_prompt=None, size=(24, 110)):
        code = self.case.mocks + "\n" + (body or "main use " + shlex.join(args))
        stdout = self.fixture.fixture / "picker.stdout"
        before = self.case.snapshot()
        pid, master = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", *size, 0, 0))
            output = os.open(stdout, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(output, 1)
            os.close(output)
            os.execve("/bin/bash", ["bash", "--noprofile", "--norc", "-c", code], self.fixture.env)
        original = termios.tcgetattr(master)
        data = b""
        sent = backup_sent = yolo_sent = False
        status = None
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.025)
                if ready:
                    try:
                        data += os.read(master, 65536)
                    except OSError:
                        pass
                if not sent and b"Profiles " in data:
                    # Discovery must not stage mode, read passwords/credentials,
                    # stop processes, or alter any profile/session file.
                    self.assertEqual(self.case.snapshot(), before)
                    self.assertEqual(self.case.operations(), [])
                    if before_select:
                        before_select()
                    os.write(master, keys)
                    sent = True
                if not backup_sent and b"Confirm outgoing backup? (y/N) " in data:
                    self.assertIsNotNone(backup, data.decode(errors="replace"))
                    self.assertEqual(self.case.operations(), [])
                    if before_prompt:
                        before_prompt()
                    os.write(master, backup)
                    backup_sent = True
                if not yolo_sent and b"YOLO? (y/N) " in data:
                    self.assertIsNotNone(yolo, data.decode(errors="replace"))
                    self.assertEqual(self.case.operations(), [])
                    os.write(master, yolo)
                    yolo_sent = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = os.waitstatus_to_exitcode(result)
                    # Drain remaining bytes after process exit.
                    while select.select([master], [], [], 0)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        data += chunk
                    break
            self.assertIsNotNone(status, data.decode(errors="replace"))
            restored = termios.tcgetattr(master)
            # Window dimensions may differ; all terminal input/output modes and
            # control characters must match, including echo, canonical and ISIG.
            self.assertEqual(restored, original)
        finally:
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(master)
        self.assertFalse(self.fixture.forbidden.exists())
        return status, data.decode(errors="replace"), stdout.read_text()

    def test_initial_current_saved_labels_and_enter_fast_path(self):
        status, menu, stdout = self.run_pty(["--no-yolo"], b"\r")
        self.assertEqual(status, 0, menu + stdout)
        self.assertIn("> sydney (active) | saved-sydney@example.test | TZ=Australia/Sydney", menu)
        self.assertIn("  tokyo | saved-tokyo@example.test | TZ=Asia/Tokyo", menu)
        self.assertNotIn("Confirm outgoing backup", menu)
        self.assertEqual(self.case.operations(), ["stage"])
        self.assertNotIn("Choose saved profile", stdout)
        self.assertFalse(json.loads((self.accounts / ".launch-mode").read_text())["yolo"])

    def test_arrows_and_jk_reach_existing_backup_and_separate_yolo_flow(self):
        for keys in (b"\x1b[B\x1b[A\x1b[B\r", b"jkj\r", b"\x1bOB\r"):
            with self.subTest(keys=keys):
                status, menu, stdout = self.run_pty(keys=keys, backup=b"y\n", yolo=b"n\n")
                self.assertEqual(status, 0, menu + stdout)
                self.assertIn("> tokyo | saved-tokyo@example.test", menu)
                self.assertLess(menu.index("Confirm outgoing backup"), menu.index("YOLO?"))
                self.case.assert_switched()
                self.case.run_body("main use sydney --yes --no-yolo")
                self.case.ops.unlink()

    def test_q_escape_ctrl_c_and_eof_cancel_without_side_effects(self):
        for keys in (b"q", b"\x1b", b"\x03", b"\x04"):
            with self.subTest(keys=keys):
                before = self.case.snapshot()
                status, menu, stdout = self.run_pty(["--yes", "--yolo"], keys)
                self.assertEqual(status, 130 if keys == b"\x03" else 1, menu + stdout)
                self.assertNotIn("YOLO?", menu)
                self.assertEqual(self.case.snapshot(), before)
                self.assertEqual(self.case.operations(), [])

    def test_cancel_at_backup_preserves_everything_after_picker(self):
        before = self.case.snapshot()
        status, menu, _ = self.run_pty(keys=b"j\r", backup=b"\n")
        self.assertEqual(status, 1, menu)
        self.assertEqual(self.case.snapshot(), before)
        self.assertEqual(self.case.operations(), [])

    def test_yes_requires_real_selection_and_only_skips_backup(self):
        status, menu, _ = self.run_pty(["--yes"], b"j\r", yolo=b"n\n")
        self.assertEqual(status, 0, menu)
        self.assertNotIn("Confirm outgoing backup", menu)
        self.assertIn("YOLO?", menu)
        self.case.assert_switched()

    def test_picker_stdout_is_name_only(self):
        status, menu, stdout = self.run_pty(body="pick_saved_account", keys=b"j\r")
        self.assertEqual(status, 0, menu)
        self.assertEqual(stdout, "tokyo\n")
        self.assertEqual(self.case.operations(), [])

    def test_no_name_launch_flags_and_yes_in_either_order(self):
        for args, expected in ((["--yolo"], True), (["--yolo", "--yes"], True),
                               (["--yes", "--yolo"], True), (["--no-yolo", "--yes"], False)):
            with self.subTest(args=args):
                status, menu, _ = self.run_pty(args, b"\r")
                self.assertEqual(status, 0, menu)
                self.assertNotIn("YOLO?", menu)
                self.assertEqual(json.loads((self.accounts / ".launch-mode").read_text())["yolo"], expected)
                self.case.ops.unlink()

    def test_hook_and_recovery_guards_precede_picker_and_account_operations(self):
        hook = self.case.home / ".ccacc-shell/hook.sh"
        saved = hook.read_bytes()
        hook.write_text("# invalid fixture hook\n")
        before = self.case.snapshot()
        status, menu, _ = self.run_pty()
        self.assertEqual(status, 1, menu)
        self.assertIn("Shell hook is not installed correctly", menu)
        self.assertEqual(self.case.snapshot(), before)
        self.assertEqual(self.case.operations(), [])
        hook.write_bytes(saved)
        for name in (".cleanup-recovery", ".profile-transaction.fixture"):
            with self.subTest(name=name):
                recovery = self.accounts / name
                recovery.mkdir()
                before = self.case.snapshot()
                status, menu, _ = self.run_pty()
                self.assertEqual(status, 1, menu)
                self.assertNotIn("Choose saved profile", menu)
                self.assertIn("recovery data retained", menu)
                self.assertEqual(self.case.snapshot(), before)
                self.assertEqual(self.case.operations(), [])
                recovery.rmdir()

    def test_nonterminal_never_consumes_password_stdin_including_yes(self):
        for args in ("", "--yes", "--yolo --yes", "--yes --no-yolo"):
            with self.subTest(args=args):
                before = self.case.snapshot()
                result = self.case.run_body("if main use " + args + "; then exit 99; fi\n"
                    'IFS= read -r remaining; printf "%s\\n" "$remaining"')
                self.assertEqual(result.stdout, "password-stdin-sentinel\n")
                self.assertIn("requires a terminal", result.stderr)
                self.assertEqual(self.case.snapshot(), before)
                self.assertEqual(self.case.operations(), [])

    def test_no_saved_profiles_has_save_instructions_without_mutation(self):
        for path in self.accounts.glob("*.json"):
            path.unlink()
        before = self.case.snapshot()
        status, menu, _ = self.run_pty()
        self.assertEqual(status, 1, menu)
        self.assertIn("ccacc save <name> --timezone <ZONE>", menu)
        self.assertEqual(self.case.snapshot(), before)
        self.assertEqual(self.case.operations(), [])

    def test_invalid_json_symlinks_and_unsafe_names_are_excluded(self):
        for name, value in (("malformed", "{bad"), ("array", "[]"), ("multiple", "{} {}"),
                            ("empty", ""), ("bad name", "{}"), (".hidden", "{}")):
            (self.accounts / (name + ".json")).write_text(value)
        (self.accounts / "linked.json").symlink_to(self.accounts / "tokyo.json")
        status, menu, _ = self.run_pty()
        self.assertEqual(status, 1)
        self.assertIn("Profiles 1-2 of 2", menu)
        for name in ("malformed", "array", "multiple", "empty", "bad name", ".hidden", "linked"):
            self.assertNotIn(name, menu)

    def test_saved_metadata_is_sanitized_and_never_evaluated(self):
        (self.accounts / "tokyo.json").write_text(json.dumps({"oauthAccount": {"emailAddress":
            "$(touch $HOME/injected)\u001b[31m\n\r\t\u007f\u009b\u202e"}}))
        status, menu, _ = self.run_pty()
        self.assertEqual(status, 1)
        self.assertIn("$(touch $HOME/injected)?[31m??????", menu)
        self.assertNotIn("\x1b[31m", menu)
        self.assertFalse((self.case.home / "injected").exists())

    def test_missing_or_invalid_timezone_display_and_selection_validation(self):
        for zone in (None, "bad\u001b[31m"):
            with self.subTest(zone=zone):
                path = self.accounts / ".timezones/tokyo"
                if path.exists():
                    path.unlink()
                if zone is not None:
                    path.write_text(zone)
                before = self.case.snapshot()
                status, menu, _ = self.run_pty(keys=b"j\r")
                self.assertEqual(status, 1, menu)
                self.assertIn("No valid saved timezone for 'tokyo'", menu)
                self.assertEqual(self.case.snapshot(), before)
                self.assertEqual(self.case.operations(), [])

    def test_target_revalidated_after_menu(self):
        def corrupt():
            (self.accounts / "tokyo.json").write_text("[]")
        status, menu, _ = self.run_pty(keys=b"j\r", before_select=corrupt)
        self.assertEqual(status, 1, menu)
        self.assertIn("Invalid saved account 'tokyo'", menu)
        self.assertEqual(self.case.operations(), [])

    def test_no_current_highlights_first_and_large_list_is_bounded(self):
        (self.accounts / ".active").unlink()
        for index in range(20):
            self.fixture.account("account%02d" % index, "UTC")
        status, menu, stdout = self.run_pty(body="pick_saved_account", keys=b"j" * 9 + b"\r",
                                            size=(9, 45))
        self.assertEqual(status, 0, menu)
        self.assertEqual(stdout, "account09\n")
        self.assertIn("> account00 | TZ=UTC", menu)
        self.assertIn("Profiles 6-10 of 22", menu)
        self.assertNotIn("account19", menu)

    def test_invalid_arguments_fail_before_any_read_boundary(self):
        for args in ("--yes --yes", "--yolo --yolo", "--no-yolo --no-yolo", "--yolo --no-yolo",
                     "--unknown", "--yes --no-yolo --yolo", "--yes ''", "--yolo ../bad",
                     "--yes tokyo extra", "--no-yolo tokyo --unknown"):
            with self.subTest(args=args):
                before = self.case.snapshot()
                self.case.run_body('require_shell_hook() { printf hook >> "$OPS"; }; '
                    'jq() { printf jq >> "$OPS"; return 99; }; main use ' + args, success=False)
                self.assertEqual(self.case.snapshot(), before)
                self.assertEqual(self.case.operations(), [])

    def test_explicit_target_accepts_options_on_either_side_without_menu(self):
        for args in ("--yes tokyo --no-yolo", "--no-yolo --yes tokyo", "tokyo --yes --no-yolo"):
            with self.subTest(args=args):
                result = self.case.run_body("main use " + args)
                self.assertNotIn("Choose saved profile", result.stderr)
                self.assertEqual((self.accounts / ".active").read_text(), "tokyo\n")
                self.case.run_body("main use sydney --yes --no-yolo")
                self.case.ops.unlink()


if __name__ == "__main__":
    unittest.main()
