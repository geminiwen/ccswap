"""Outgoing-backup consent with real file transitions and fake system IO only."""
import json
import os
import pty
import select
import shlex
import signal
import time
import unittest

import test_ccacc_shell_integration as integration


MOCKS = r'''
source "$FIXTURE_CORE" help >/dev/null
read_credentials() { printf 'read\n' >> "$OPS"; cat "$CREDENTIALS_FILE"; }
write_credentials() { printf 'write\n' >> "$OPS"; printf '%s\n' "$1" > "$CREDENTIALS_FILE"; }
delete_credentials() { printf 'delete\n' >> "$OPS"; rm -f "$CREDENTIALS_FILE"; }
encrypt_to() { cat > "$1"; }
decrypt_from() { cat "$1"; }
ask_password() { printf 'password\n' >> "$OPS"; MASTER_PASSWORD=fixture; }
stop_claude() { printf 'stop\n' >> "$OPS"; }
note_device_keys() { :; }
apply_surge() { printf 'surge\n' >> "$OPS"; }
mktemp() { printf 'stage\n' >> "$OPS"; command mktemp "$@"; }
'''


class UseConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ShellIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        self.accounts = self.fixture.accounts
        self.fixture.startup("bash")
        self.fixture.cli("configure-shell")
        self.ops = self.fixture.fixture / "operations"
        self.fixture.env["OPS"] = str(self.ops)
        self.credentials = self.home / ".claude/.credentials.json"
        self.credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "latest-login-secret"},
                                               "mcpOAuth": {"shared": "shared-secret"}}))
        (self.home / ".claude.json").write_text(json.dumps({"userID": "latest-id-secret",
            "numStartups": 42, "projects": {"private-project-secret": {}},
            "mcpServers": {"shared": {"url": "shared-url-secret"}}}))
        (self.accounts / "sydney.json").write_text('{"userID":"old-saved-id"}')
        (self.accounts / "sydney.credentials.enc").write_text('{"claudeAiOauth":{"accessToken":"old-secret"}}')
        (self.accounts / "tokyo.json").write_text('{"userID":"target-id"}')
        (self.accounts / "tokyo.credentials.enc").write_text('{"claudeAiOauth":{"accessToken":"target-secret"}}')
        for name in ("sydney", "tokyo"):
            (self.accounts / (name + ".surge")).write_text('{"Claude":"saved-route"}')
        (self.accounts / ".launch-mode").write_text('{"version":1,"account":"sydney","yolo":true}\n')
        (self.accounts / ".launch-mode").chmod(0o600)
        (self.home / ".claude/history.jsonl").write_text("latest-history-secret\n")
        drafts = self.home / ".claude/feedback/drafts"
        drafts.mkdir(parents=True)
        (drafts / "draft").write_text("draft-secret")
        cache = self.home / "Library/Caches/claude-cli-nodejs"
        cache.mkdir(parents=True)
        (cache / "cache").write_text("cache-secret")
        self.sessions = self.home / ".claude/sessions"
        self.sessions.mkdir()
        self.session_bytes = {
            "fixture-session.json": b'{"id":"synthetic-sydney-session","pid":12345}\n',
            "fixture-session.key": b"\x00\xffsynthetic-session-key\x01\n",
        }
        for name, content in self.session_bytes.items():
            (self.sessions / name).write_bytes(content)
        # Exercise the same presence predicate as stash_paths, including broken links.
        (self.home / ".claude/plans").symlink_to("absent-fixture-plan")
        # Preserve real implementations while observing ordering at their boundaries.
        self.mocks = MOCKS
        for name in ("save_account", "load_account", "stash_paths", "unstash_paths"):
            self.mocks += '\neval "$(declare -f ' + name + " | sed '1s/" + name + "/original_" + name + "/')\"\n"
            self.mocks += name + '() { printf "' + name + '\\n" >> "$OPS"; original_' + name + ' "$@"; }\n'

    def snapshot(self):
        result = {}
        for p in self.home.rglob("*"):
            stat = p.lstat()
            value = os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None
            result[str(p.relative_to(self.home))] = (value, stat.st_mtime_ns, stat.st_ino, stat.st_mode)
        return result

    def operations(self):
        return self.ops.read_text().splitlines() if self.ops.exists() else []

    def run_body(self, body, success=True, stdin="password-stdin-sentinel\n"):
        return self.fixture.run_process(["/bin/bash", "-c", self.mocks + "\n" + body],
                                        success=success, stdin=stdin)

    def run_pty(self, args, answer, yolo_answer=None):
        code = self.mocks + "\nmain use " + shlex.join(args)
        stdout = self.fixture.fixture / "pty.stdout"
        before = self.snapshot()
        pid, master = pty.fork()
        if pid == 0:
            output = os.open(stdout, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(output, 1)
            os.close(output)
            os.execve("/bin/bash", ["bash", "--noprofile", "--norc", "-c", code], self.fixture.env)
        data = b""
        status = None
        sent = False
        yolo_sent = False
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        data += os.read(master, 65536)
                    except OSError:
                        pass
                if not sent and b"Confirm outgoing backup? (y/N) " in data:
                    # No staged mode, passwords, credentials, backup or process work yet.
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.operations(), [])
                    os.write(master, answer)
                    sent = True
                if not yolo_sent and b"YOLO? (y/N) " in data:
                    self.assertIsNotNone(yolo_answer, data.decode(errors="replace"))
                    os.write(master, yolo_answer)
                    yolo_sent = True
                done, result = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = os.waitstatus_to_exitcode(result)
                    break
            self.assertIsNotNone(status, data.decode(errors="replace"))
        finally:
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(master)
        self.assertFalse(self.fixture.forbidden.exists())
        return status, data.decode(errors="replace"), stdout.read_text()

    def assert_switched(self):
        self.assertEqual((self.accounts / ".active").read_text(), "tokyo\n")
        saved = json.loads((self.accounts / "sydney.json").read_text())
        self.assertEqual(saved["userID"], "latest-id-secret")
        self.assertEqual(saved["numStartups"], 42)
        self.assertNotIn("mcpServers", saved)
        self.assertEqual(json.loads((self.accounts / "sydney.credentials.enc").read_text()),
                         {"claudeAiOauth": {"accessToken": "latest-login-secret"}})
        self.assertEqual((self.accounts / "sydney/history.jsonl").read_text(), "latest-history-secret\n")
        self.assertFalse((self.home / ".claude/history.jsonl").exists())
        self.assertEqual((self.accounts / "sydney/feedback-drafts/draft").read_text(), "draft-secret")
        self.assertEqual({p.name: p.read_bytes() for p in (self.accounts / "sydney/sessions").iterdir()},
                         self.session_bytes)
        self.assertFalse(self.sessions.exists())
        self.assertTrue((self.accounts / "sydney/plans").is_symlink())
        self.assertEqual(json.loads(self.credentials.read_text())["mcpOAuth"], {"shared": "shared-secret"})
        self.assertEqual((self.accounts / ".timezones/sydney").read_text(), "Australia/Sydney\n")
        self.assertEqual((self.accounts / "sydney.surge").read_text(), '{"Claude":"saved-route"}')
        ops = self.operations()
        for earlier, later in zip(("password", "stop", "save_account", "load_account", "stash_paths"),
                                  ("stop", "save_account", "load_account", "stash_paths", "unstash_paths")):
            self.assertLess(ops.index(earlier), ops.index(later))

    def test_yes_saves_latest_state_with_precise_secret_free_preview(self):
        status, stderr, stdout = self.run_pty(["tokyo", "--no-yolo"], b"YeS\n")
        self.assertEqual(status, 0, stderr + stdout)
        self.assertIn("profile 'sydney', then switch to 'tokyo'", stderr)
        self.assertIn("Encrypted Claude login credentials (claudeAiOauth only)", stderr)
        self.assertNotIn("Account-bound ~/.claude.json keys present:", stderr)
        for path in ("~/.claude/history.jsonl", "~/.claude/feedback/drafts", "~/.claude/plans", "~/.claude/sessions",
                     "~/Library/Caches/claude-cli-nodejs"):
            self.assertIn(path, stderr)
        self.assertNotIn("~/.claude/telemetry", stderr)
        self.assertIn("move into ~/.claude/accounts/sydney/ stash", stderr)
        self.assertIn("timezone (TZ) and Surge choices remain unchanged", stderr)
        for secret in ("latest-login-secret", "shared-secret", "latest-id-secret", "private-project-secret",
                       "latest-history-secret", "draft-secret", "cache-secret", "shared-url-secret", "target-secret"):
            self.assertNotIn(secret, stderr + stdout)
        self.assert_switched()
        self.assertFalse(json.loads((self.accounts / ".launch-mode").read_text())["yolo"])

    def test_sessions_roundtrip_moves_exact_json_and_key_bytes(self):
        target = self.accounts / "tokyo/sessions"
        target.mkdir(parents=True)
        target_bytes = {
            "target.json": b'{"id":"synthetic-tokyo-session"}\n',
            "target.key": b"\xfe\x00synthetic-target-key\x02",
        }
        for name, content in target_bytes.items():
            (target / name).write_bytes(content)
        self.run_body("main use tokyo --yes --no-yolo")
        self.assertFalse(target.exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.sessions.iterdir()}, target_bytes)
        self.assertEqual({p.name: p.read_bytes() for p in (self.accounts / "sydney/sessions").iterdir()},
                         self.session_bytes)
        target_bytes["next.json"] = b'{"id":"synthetic-new-tokyo-session"}\n'
        (self.sessions / "next.json").write_bytes(target_bytes["next.json"])
        self.run_body("main use sydney --yes --no-yolo")
        self.assertEqual({p.name: p.read_bytes() for p in self.sessions.iterdir()}, self.session_bytes)
        self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, target_bytes)
        self.assertFalse((self.accounts / "sydney/sessions").exists())

    def test_use_isolates_account_caches_with_legacy_and_current_backups(self):
        caches = {
            "cachedArtifactRoster": {"accountWuid": "outgoing-account", "orgWuid": "outgoing-org"},
            "cachedUsageUtilization": {"orgWuid": "outgoing-org", "utilization": 42},
        }
        target_caches = {
            "cachedArtifactRoster": {"accountWuid": "target-account", "orgWuid": "target-org"},
            "cachedUsageUtilization": {"orgWuid": "target-org", "utilization": 7},
        }
        config_path = self.home / ".claude.json"
        for incoming in ({}, target_caches):
            with self.subTest(incoming=incoming):
                config = json.loads(config_path.read_text())
                config.update(caches)
                config_path.write_text(json.dumps(config))
                (self.accounts / "tokyo.json").write_text(json.dumps({"userID": "target-id", **incoming}))
                self.run_body("main use tokyo --yes --no-yolo")
                saved = json.loads((self.accounts / "sydney.json").read_text())
                switched = json.loads(config_path.read_text())
                for key, value in caches.items():
                    self.assertEqual(saved[key], value)
                    if key in incoming:
                        self.assertEqual(switched[key], incoming[key])
                    else:
                        self.assertNotIn(key, switched)
                self.assertEqual(switched["mcpServers"], config["mcpServers"])
                self.run_body("main use sydney --yes --no-yolo")
                restored = json.loads(config_path.read_text())
                for key, value in caches.items():
                    self.assertEqual(restored[key], value)

    def test_no_empty_eof_and_ctrl_c_preserve_all_state(self):
        for answer in (b"n\n", b"\n", b"\x04", b"\x03", b"unexpected\n"):
            with self.subTest(answer=answer):
                before = self.snapshot()
                status, stderr, _ = self.run_pty(["tokyo"], answer)
                self.assertEqual(status, 130 if answer == b"\x03" else 1, stderr)
                self.assertNotIn("YOLO?", stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.operations(), [])

    def test_nonterminal_rejects_without_password_or_consuming_stdin(self):
        before = self.snapshot()
        result = self.run_body(r'''
ask_password() { printf 'password\n' >> "$OPS"; read -r password; }
if main use tokyo --yolo; then exit 99; fi
IFS= read -r remaining
printf '%s\n' "$remaining"
''')
        self.assertEqual(result.stdout, "password-stdin-sentinel\n")
        self.assertIn("ccacc use tokyo --yes", result.stderr)
        self.assertIn("does not enable YOLO", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.operations(), [])

    def test_explicit_yes_bypasses_only_backup_confirmation(self):
        result = self.run_body("main use tokyo --yes")
        self.assertNotIn("Confirm outgoing backup", result.stderr)
        self.assert_switched()
        self.assertFalse(json.loads((self.accounts / ".launch-mode").read_text())["yolo"])

    def test_yes_still_prompts_for_yolo_on_terminal(self):
        status, stderr, _ = self.run_pty(["tokyo", "--yes"], b"unused\n", yolo_answer=b"n\n")
        self.assertEqual(status, 0, stderr)
        self.assertNotIn("Confirm outgoing backup", stderr)
        self.assertIn("YOLO? (y/N)", stderr)
        self.assert_switched()
        self.assertFalse(json.loads((self.accounts / ".launch-mode").read_text())["yolo"])

    def test_options_in_either_order_and_explicit_yolo(self):
        for args, expected in (("--yes --no-yolo", False), ("--no-yolo --yes", False),
                               ("--yes --yolo", True), ("--yolo --yes", True)):
            with self.subTest(args=args):
                self.run_body("main use tokyo " + args)
                self.assertEqual(json.loads((self.accounts / ".launch-mode").read_text())["yolo"], expected)
                self.run_body("main use sydney --yes --no-yolo")

    def test_bad_options_fail_before_any_system_boundary(self):
        for args in ("tokyo --yes --yes", "tokyo --yolo --yolo", "tokyo --no-yolo --no-yolo",
                     "tokyo --yes --yolo --no-yolo", "tokyo --no-yolo --yes --yolo",
                     "tokyo --yes --unknown", "tokyo extra", "tokyo ''"):
            with self.subTest(args=args):
                before = self.snapshot()
                self.run_body('require_shell_hook() { printf hook >> "$OPS"; }; main use ' + args, success=False)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.operations(), [])

    def test_same_active_skips_backup_and_restore_but_selects_yolo(self):
        before = {p: p.read_bytes() for p in (self.credentials, self.home / ".claude.json",
                  self.accounts / "sydney.json", self.accounts / "sydney.credentials.enc")}
        result = self.run_body("main use sydney --yolo")
        self.assertNotIn("Confirm outgoing backup", result.stderr)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.operations(), ["stage"])
        self.assertTrue(json.loads((self.accounts / ".launch-mode").read_text())["yolo"])


if __name__ == "__main__":
    unittest.main()
