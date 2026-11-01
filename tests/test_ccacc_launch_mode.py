"""Selection-only YOLO state with fixture accounts, fake CLI and terminal input."""
import json
import os
import pty
import select
import shlex
import time
import unittest

import test_ccacc_shell_integration as integration


class LaunchModeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ShellIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        self.accounts = self.fixture.accounts
        self.mode = self.accounts / ".launch-mode"
        self.fixture.startup("bash")
        self.fixture.cli("configure-shell")

    def seed(self, account="sydney", yolo=True):
        self.mode.write_text(json.dumps({"version": 1, "account": account, "yolo": yolo}) + "\n")
        self.mode.chmod(0o600)

    def record(self):
        return json.loads(self.mode.read_text())

    def launch(self, args, shell="bash"):
        flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
        return self.fixture.run_process(["/bin/" + shell, *flags, "-c",
            '. "$HOME/.ccacc-shell/hook.sh"\nclaude ' + shlex.join(args)], stdin="payload\n")

    def selection_pty(self, target, answer, extra=""):
        # Every credential/process boundary is fake; selection, state staging,
        # installed checks and active-marker writes use the actual core code.
        code = r'''
source "$FIXTURE_CORE" help >/dev/null
migrate_encrypted_backups() { printf 'password\n' >> "$HOME/selection-operations"; }
stop_claude() { printf 'stop\n' >> "$HOME/selection-operations"; }
# Real process checks are covered by test_ccacc_close_prompt.
confirm_close_claude() { :; }
note_device_keys() { :; }
backup_active() { :; }
load_account() { :; }
stash_paths() { :; }
unstash_paths() { :; }
apply_surge() { :; }
'''
        code += extra + "\nmain use " + shlex.quote(target)
        stdout_path = self.fixture.fixture / "selection.stdout"
        pid, master = pty.fork()
        if pid == 0:
            output = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(output, 1)
            os.close(output)
            os.execve("/bin/bash", ["bash", "--noprofile", "--norc", "-c", code], self.fixture.env)
        stderr = b""
        status = None
        sent = False
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        stderr += os.read(master, 65536)
                    except OSError:
                        pass
                if not sent and b"YOLO? (y/N) " in stderr:
                    os.write(master, answer)
                    sent = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = os.waitstatus_to_exitcode(result)
                    break
            self.assertIsNotNone(status, stderr.decode(errors="replace"))
        finally:
            os.close(master)
        self.assertFalse(self.fixture.forbidden.exists())
        return status, stderr.decode(errors="replace"), stdout_path.read_text()

    def test_use_pty_yes_no_blank_eof_and_ctrl_c(self):
        for answer, expected in ((b"y\n", True), (b"YES\n", True), (b"n\n", False),
                                 (b"\n", False), (b"\x04", False)):
            with self.subTest(answer=answer):
                (self.accounts / ".active").write_text("sydney\n")
                self.seed()
                status, stderr, stdout = self.selection_pty("tokyo", answer)
                self.assertEqual(status, 0, stderr + stdout)
                self.assertEqual(stderr.count("YOLO? (y/N) "), 1)
                self.assertNotIn("YOLO", stdout)
                self.assertEqual(self.record(), {"version": 1, "account": "tokyo", "yolo": expected})
                self.assertEqual(self.mode.stat().st_mode & 0o777, 0o600)
                self.assertEqual(self.fixture.cli("launch-mode").stdout, str(expected).lower() + "\n")
        self.seed("tokyo")
        before = self.mode.read_bytes()
        status, _, _ = self.selection_pty("sydney", b"\x03")
        self.assertEqual(status, 130)
        self.assertEqual(self.mode.read_bytes(), before)
        self.assertEqual((self.accounts / ".active").read_text(), "tokyo\n")
        self.assertFalse(self.fixture.launches.exists())

    def test_same_active_can_toggle_without_passwords_or_rc_writes(self):
        rcs = [self.home / ".bashrc", self.home / ".bash_profile"]
        paths = rcs + list((self.home / ".ccacc-shell").iterdir())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}
        for answer, enabled in ((b"y\n", True), (b"\n", False), (b"n\n", False)):
            status, stderr, _ = self.selection_pty("sydney", answer,
                "migrate_encrypted_backups() { return 97; }; stop_claude() { return 98; }")
            self.assertEqual(status, 0, stderr)
            self.assertEqual(self.record()["yolo"], enabled)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths})
        self.assertFalse((self.home / "selection-operations").exists())

    def test_non_tty_defaults_no_without_consuming_password_input(self):
        self.seed()
        result = self.fixture.harness(r'''
migrate_encrypted_backups() { IFS= read -r password; printf '%s\n' "$password" > "$HOME/password-input"; }
main use tokyo
''', stdin="fixture-password\n")
        self.assertNotIn("YOLO", result.stderr)
        self.assertEqual((self.home / "password-input").read_text(), "fixture-password\n")
        self.assertFalse(self.record()["yolo"])
        self.fixture.harness("main use tokyo --yolo", stdin="")
        self.assertTrue(self.record()["yolo"])
        self.fixture.harness("main use tokyo --no-yolo", stdin="")
        self.assertFalse(self.record()["yolo"])

    def test_failed_use_keeps_previous_mode_and_cleans_staging(self):
        for failure in ("migrate_encrypted_backups", "stop_claude", "backup_active", "load_account",
                        "stash_paths", "unstash_paths", "apply_surge"):
            with self.subTest(failure=failure):
                (self.accounts / ".active").write_text("sydney\n")
                self.seed()
                before = self.mode.read_bytes(), self.mode.stat().st_mtime_ns, self.mode.stat().st_ino
                self.fixture.harness(f"{failure}() {{ return 91; }}; main use tokyo --no-yolo", success=False)
                self.assertEqual((self.mode.read_bytes(), self.mode.stat().st_mtime_ns, self.mode.stat().st_ino), before)
                self.assertEqual(list(self.accounts.glob(".launch-mode.????????")), [])
        for body in ("main use missing --yolo", "main use tokyo --bad", "main use tokyo --yolo --no-yolo"):
            self.seed()
            before = self.mode.read_bytes()
            self.fixture.harness(body, success=False)
            self.assertEqual(self.mode.read_bytes(), before)
        self.seed()
        before = self.mode.read_bytes()
        (self.accounts / ".timezones/tokyo").write_text("Invalid/Zone\n")
        self.fixture.harness("main use tokyo --no-yolo", success=False)
        self.assertEqual(self.mode.read_bytes(), before)

    def test_staging_failure_precedes_password_process_and_account_mutation(self):
        self.seed()
        before = self.mode.read_bytes()
        self.fixture.harness(r'''
chmod() { return 91; }
main use tokyo --no-yolo
''', success=False)
        self.assertEqual(self.mode.read_bytes(), before)
        self.assertFalse((self.home / "operations").exists())
        self.assertEqual((self.accounts / ".active").read_text(), "sydney\n")
        self.assertEqual(list(self.accounts.glob(".launch-mode.????????")), [])

    def test_bad_install_and_unknown_target_fail_before_prompt_or_mode_change(self):
        self.seed()
        before = self.mode.read_bytes(), self.mode.stat().st_mtime_ns
        status, stderr, _ = self.selection_pty("missing", b"y\n")
        self.assertNotEqual(status, 0)
        self.assertNotIn("YOLO", stderr)
        hook = self.home / ".ccacc-shell/hook.sh"
        hook.write_text("unverified fixture code\n")
        status, stderr, _ = self.selection_pty("tokyo", b"y\n")
        self.assertNotEqual(status, 0)
        self.assertNotIn("YOLO", stderr)
        self.assertEqual((self.mode.read_bytes(), self.mode.stat().st_mtime_ns), before)
        self.assertFalse((self.home / "selection-operations").exists())

    def test_failed_mode_publish_does_not_replace_previous_choice(self):
        self.seed()
        before = self.mode.read_bytes()
        self.fixture.harness(r'''
mv() { if [ "$3" = "$LAUNCH_MODE_FILE" ]; then return 91; fi; command mv "$@"; }
main use tokyo --no-yolo
''', success=False)
        self.assertEqual(self.mode.read_bytes(), before)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")

    def test_absent_corrupt_stale_and_symlink_mode_never_enable_bypass(self):
        for content in (None, "{bad", "null", "[]", '{"version":1,"account":"sydney","yolo":"true"}',
                        '{"version":1,"account":"tokyo","yolo":true}',
                        '{"version":2,"account":"sydney","yolo":true}',
                        '{"version":1,"account":"sydney","yolo":true,"extra":true}'):
            with self.subTest(content=content):
                self.mode.unlink(missing_ok=True)
                if content is not None:
                    self.mode.write_text(content)
                self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
                self.launch(["--help", "opaque arg"])
                self.assertEqual(self.fixture.records(self.fixture.launches)[-1]["args"], ["--help", "opaque arg"])
        self.mode.unlink()
        target = self.home / "mode-target"
        target.write_text('{"version":1,"account":"sydney","yolo":true}')
        self.mode.symlink_to(target)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
        self.fixture.harness("main use sydney --yolo", success=False)
        self.assertTrue(self.mode.is_symlink())

    def test_no_active_ignores_stale_mode_and_unsets_timezone(self):
        self.seed()
        (self.accounts / ".active").unlink()
        self.fixture.env["TZ"] = "America/New_York"
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
        self.launch(["/login"])
        entry = self.fixture.records(self.fixture.launches)[0]
        self.assertEqual(entry["args"], ["/login"])
        self.assertIsNone(entry["tz"])
        self.assertEqual(entry["browser"], "false")

    def test_chosen_mode_prepends_one_flag_without_classifying_caller_argv(self):
        self.fixture.harness("main use sydney --yolo")
        for shell in ("bash", "zsh"):
            for args in (["--help"], ["--version"], ["/login"], ["-p", "two words", ""],
                         ["--dangerously-skip-permissions", "literal;value"]):
                self.launch(args, shell)
                self.assertEqual(self.fixture.records(self.fixture.launches)[-1]["args"],
                                 ["--dangerously-skip-permissions", *args])

    def test_selected_launch_consumes_no_stdin_and_preserves_exit_status(self):
        self.fixture.harness("main use sydney --yolo")
        self.fixture.executable("claude", '#!/bin/bash\nIFS= read -r input\n'
                                'printf "%s\\n" "$input"\nprintf "[%s]\\n" "$@"\nexit 37\n')
        for shell in ("bash", "zsh"):
            flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
            result = self.fixture.run_process(["/bin/" + shell, *flags, "-c",
                '. "$HOME/.ccacc-shell/hook.sh"; claude -p "two words" ""'],
                success=False, stdin="payload survives\n")
            self.assertEqual(result.returncode, 37)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout,
                             "payload survives\n[--dangerously-skip-permissions]\n[-p]\n[two words]\n[]\n")

    def test_invalid_same_active_profile_and_recovery_guard_cannot_update_mode(self):
        self.seed()
        before = self.mode.read_bytes()
        profile = self.accounts / "sydney.json"
        profile.write_text("[]")
        status, stderr, _ = self.selection_pty("sydney", b"y\n")
        self.assertNotEqual(status, 0)
        self.assertNotIn("YOLO", stderr)
        self.assertEqual(self.mode.read_bytes(), before)
        profile.write_text("{}")
        (self.accounts / ".cleanup-recovery").mkdir()
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
        self.fixture.harness("main use sydney --no-yolo", success=False)
        self.assertEqual(self.mode.read_bytes(), before)

    def test_getter_is_readonly_without_credentials_or_installed_checks(self):
        self.seed()
        (self.home / ".claude.json").unlink()
        self.fixture.core.with_name("ccacc_shell.sh").unlink()
        (self.home / ".ccacc-shell/hook.sh").write_text("unverified fixture code\n")
        before = self.fixture.home_snapshot()
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "true\n")
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.fixture.env["HOME"] = "relative-fixture-home"
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")

    def remove_profile(self, name, stdin="y\n", success=True):
        return self.fixture.run_process(["/bin/bash", str(self.fixture.core), "rm", name],
                                        stdin=stdin, success=success)

    def test_removed_name_cannot_resurrect_previous_yolo_choice(self):
        self.fixture.harness("main use sydney --yolo")
        self.remove_profile("sydney")
        self.assertFalse(self.mode.exists())
        self.fixture.harness("main save sydney --timezone UTC")
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
        self.launch(["-p", "opaque caller arg"])
        self.assertEqual(self.fixture.records(self.fixture.launches)[-1]["args"], ["-p", "opaque caller arg"])

    def test_renaming_and_reusing_name_cannot_resurrect_previous_choice(self):
        self.fixture.harness("main use sydney --yolo")
        self.fixture.harness("main save renamed --timezone UTC")
        self.assertFalse(self.mode.exists())
        self.remove_profile("sydney")
        self.fixture.harness("main save sydney --timezone UTC")
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")

    def test_save_replacing_identity_under_same_name_invalidates_choice(self):
        profile = self.accounts / "sydney.json"
        profile.write_text('{"userID":"old-identity"}')
        self.fixture.harness("main use sydney --yolo")
        (self.home / ".claude.json").write_text('{"userID":"new-identity"}')
        self.fixture.harness(r'''
save_account() { command jq '{userID}' "$CLAUDE_JSON" > "$ACCOUNTS_DIR/$1.json"; }
main save sydney --timezone UTC
''')
        self.assertEqual(json.loads(profile.read_text())["userID"], "new-identity")
        self.assertFalse(self.mode.exists())
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")

    def test_removing_unrelated_profile_preserves_retained_active_choice(self):
        self.fixture.harness("main use sydney --yolo")
        self.mode.chmod(0o400)
        before = self.mode.read_bytes(), self.mode.stat().st_mtime_ns, self.mode.stat().st_ino
        self.remove_profile("tokyo")
        self.assertEqual((self.mode.read_bytes(), self.mode.stat().st_mtime_ns, self.mode.stat().st_ino), before)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "true\n")

    def test_deleting_dormant_mode_owner_invalidates_only_that_choice(self):
        self.seed("tokyo")
        self.remove_profile("tokyo")
        self.assertFalse(self.mode.exists())
        self.assertEqual((self.accounts / ".active").read_text(), "sydney\n")
        self.assertTrue((self.accounts / "sydney.json").exists())

    def test_unsafe_mode_path_blocks_save_and_delete_before_profile_changes(self):
        target = self.home / "mode-link-target"
        target.write_text('{"version":1,"account":"sydney","yolo":true}')
        self.mode.symlink_to(target)
        before = self.fixture.home_snapshot()
        self.remove_profile("sydney", success=False)
        self.fixture.harness("main save renamed --timezone UTC", success=False)
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertTrue(self.mode.is_symlink())

    def test_cancelled_delete_and_failed_password_preserve_prior_state(self):
        self.fixture.harness("main use sydney --yolo")
        before = self.fixture.home_snapshot()
        for answer in ("n\n", "\n", ""):
            self.remove_profile("sydney", stdin=answer, success=False)
            self.assertEqual(self.fixture.home_snapshot(), before)
        self.fixture.harness("migrate_encrypted_backups() { return 91; }; main save renamed --timezone UTC", success=False)
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "true\n")

    def test_mode_permission_failure_precedes_profile_deletion_and_save(self):
        self.fixture.harness("main use sydney --yolo")
        self.mode.chmod(0o400)
        if os.access(self.mode, os.W_OK):
            self.skipTest("Current user can write mode-0400 files")
        before = self.fixture.home_snapshot()
        self.remove_profile("sydney", success=False)
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.fixture.harness("main save renamed --timezone UTC", success=False)
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertFalse((self.home / "operations").exists())

    def test_failed_save_restores_profile_and_runtime_choice(self):
        self.fixture.harness("main use sydney --yolo")
        profile = self.accounts / "sydney.json"
        profile.write_text('{"userID":"prior-identity"}')
        before = self.fixture.home_snapshot()
        self.fixture.harness(r'''
migrate_encrypted_backups() { :; }
save_account() { printf '{"userID":"replacement"}' > "$ACCOUNTS_DIR/$1.json"; return 91; }
main save sydney --timezone UTC
''', success=False)
        self.assertEqual(profile.read_text(), '{"userID":"prior-identity"}')
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "true\n")

    def test_failed_delete_staging_restores_target_and_runtime_choice(self):
        self.fixture.harness("main use sydney --yolo")
        (self.accounts / "sydney.credentials").write_text("fixture encrypted backup")
        before = self.fixture.home_snapshot()
        self.fixture.harness(r'''
mv() { if [ "$1" = "$ACCOUNTS_DIR/sydney.credentials" ]; then return 91; fi; command mv "$@"; }
main rm sydney
''', success=False)
        self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "true\n")
        self.assertEqual(list(self.accounts.glob(".profile-transaction.*")), [])

    def test_incomplete_rollback_keeps_choice_disabled_and_private_restore_paths(self):
        self.fixture.harness("main use sydney --yolo")
        result = self.fixture.harness(r'''
migrate_encrypted_backups() { :; }
save_account() { printf '{"userID":"replacement"}' > "$ACCOUNTS_DIR/$1.json"; return 91; }
mv() {
    case "$1" in
        "$ACCOUNTS_DIR"/.profile-transaction.*/2)
            if [ "$2" = "$ACCOUNTS_DIR/sydney.json" ]; then return 92; fi ;;
    esac
    command mv "$@"
}
main save sydney --timezone UTC
''', success=False)
        self.assertIn("rollback incomplete", result.stderr)
        self.assertIn("manifest.json", result.stderr)
        self.assertFalse(self.mode.exists())
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")
        recovery, = self.accounts.glob(".profile-transaction.*")
        self.assertEqual(recovery.stat().st_mode & 0o777, 0o700)
        manifest = json.loads((recovery / "manifest.json").read_text())
        self.assertEqual(manifest[0]["source"], str(self.mode))
        self.assertEqual(json.loads((recovery / "0").read_text())["yolo"], True)
        before = self.fixture.home_snapshot()
        for command in ("main save another --timezone UTC", "main use sydney --yolo",
                        "main new", "main rm sydney"):
            with self.subTest(command=command):
                blocked = self.fixture.harness(command, success=False)
                self.assertIn("recovery", blocked.stderr.lower())
                self.assertEqual(self.fixture.home_snapshot(), before)
        self.assertEqual(self.fixture.cli("timezone", success=False).stdout, "")

    def test_surge_write_failure_is_not_reported_as_success(self):
        code = r'''
source "$FIXTURE_CORE" help >/dev/null
find_surge() { SURGE_CLI=fixture_surge; }
surge_groups() { builtin printf '%s' '{"Claude":["route"]}'; }
fixture_surge() {
    builtin printf '%s' '{"environment":{"ProxyGroupSelection":{"Claude":"route"}}}'
}
printf() {
    if [ "$#" = 2 ] && [ "$1" = '%s\n' ] && [ "$2" = '{"Claude":"route"}' ]; then return 91; fi
    builtin printf "$@"
}
if setup_surge sydney; then
    echo 'unexpected successful policy write' >&2
    exit 99
fi
'''
        result = self.fixture.run_process(["/bin/bash", "-c", code], stdin="1\n1\n")
        self.assertNotIn("Saved Surge policies", result.stdout)

    def test_save_invalidates_runtime_choice_without_storing_it_in_backups(self):
        self.fixture.harness("main use sydney --yolo")
        self.fixture.harness("main save sydney --timezone UTC")
        self.assertFalse(self.mode.exists())
        self.assertEqual(json.loads((self.accounts / "sydney.json").read_text()), {})
        self.fixture.harness("main save renamed --timezone UTC")
        self.assertFalse(self.mode.exists())
        self.assertEqual(self.fixture.cli("launch-mode").stdout, "false\n")


if __name__ == "__main__":
    unittest.main()
