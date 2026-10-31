"""Unnamed-session transitions with fixture files and fake crypto/login/processes."""
import json
import shlex
import shutil
import unittest

import test_ccacc_shell_integration as integration


class UnnamedSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ShellIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        self.accounts = self.fixture.accounts
        shutil.rmtree(self.home / ".claude")
        (self.home / ".claude.json").unlink()
        _, self.rcs = self.fixture.startup("bash")
        self.fixture.cli("install")
        self.ops = self.fixture.fixture / "session-operations"
        self.fixture.env["SESSION_OPERATIONS"] = str(self.ops)
        self.pending = self.accounts / ".pending-timezone"
        self.credentials = self.home / ".claude/.credentials.json"
        self.config = self.home / ".claude.json"
        self.active = self.accounts / ".active"
        self.shared = {"mcpOAuth": {"fixture": {"accessToken": "shared"}}, "futureField": {"keep": True}}

    def run_body(self, body, success=True, stdin="yes\n", process_mocks=True):
        # Only these boundaries are fake. All metadata, backup staging, bounded
        # config reset, history stashing and installation checks use real code.
        mocks = r'''
source "$FIXTURE_CORE" help >/dev/null
read_credentials() {
    printf 'read\n' >> "$SESSION_OPERATIONS"
    if [ -f "$CREDENTIALS_FILE" ]; then cat "$CREDENTIALS_FILE"; fi
}
write_credentials() { printf 'write\n' >> "$SESSION_OPERATIONS"; printf '%s\n' "$1" > "$CREDENTIALS_FILE"; }
delete_credentials() { printf 'delete\n' >> "$SESSION_OPERATIONS"; rm -f "$CREDENTIALS_FILE"; }
# Deliberately plaintext fixture backups: never use real credentials or encryption.
encrypt_to() { cat > "$1"; }
decrypt_from() { cat "$1"; }
ask_password() { printf 'password\n' >> "$SESSION_OPERATIONS"; MASTER_PASSWORD=fixture; }
stop_claude() { printf 'stop\n' >> "$SESSION_OPERATIONS"; }
note_device_keys() { :; }
setup_surge() { printf 'setup-surge\n' >> "$SESSION_OPERATIONS"; }
apply_surge() { printf 'apply-surge\n' >> "$SESSION_OPERATIONS"; }
new_identity() { printf '{"userID":"new-fixture-id","anonymousId":"new-anonymous","machineID":"new-machine"}'; }
'''
        if process_mocks:
            mocks += "\ncleanup_stop_processes() { stop_claude; }\ncleanup_runtime_is_safe() { :; }\n"
        return self.fixture.run_process(["/bin/bash", "-c", mocks + "\n" + body],
                                        success=success, stdin=stdin)

    def snapshot(self):
        return {p.relative_to(self.home): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
                for p in self.home.rglob("*") if p.is_file()}

    def hook_snapshot(self):
        paths = list(self.rcs) + list((self.home / ".ccacc-shell").iterdir())
        return {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}

    def operations(self):
        return self.ops.read_text().splitlines() if self.ops.exists() else []

    def login(self, name="fixture@example.invalid"):
        self.credentials.write_text(json.dumps({**self.shared, "claudeAiOauth": {"accessToken": "fixture-login"}}))
        config = json.loads(self.config.read_text())
        config["oauthAccount"] = {"emailAddress": name}
        self.config.write_text(json.dumps(config))

    def saved_active(self):
        self.accounts.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"userID": "old-id", "mcpServers": {"shared": {}},
                                           "projects": {"old": {}}, "settings": {"keep": True}}))
        self.login()
        (self.accounts / "kept.json").write_bytes(self.config.read_bytes())
        (self.accounts / "kept.credentials.enc").write_text('{"claudeAiOauth":{"accessToken":"prior-backup"}}')
        metadata = self.accounts / ".timezones"
        metadata.mkdir()
        (metadata / "kept").write_text("UTC\n")
        (self.accounts / "kept.surge").write_text('{"Claude":"retained route"}')
        self.active.write_text("kept\n")
        projects = self.home / ".claude/projects"
        projects.mkdir()
        (projects / "history.json").write_text("fixture history")
        (self.home / ".claude/settings.json").write_text("shared settings")

    def test_fresh_bootstrap_new_fake_login_and_named_save(self):
        for rc in self.rcs:
            rc.chmod(0o444)
        hook = self.hook_snapshot()
        self.assertFalse(self.config.exists())
        self.run_body("main new --timezone Asia/Tokyo")
        self.assertFalse(self.active.exists())
        self.assertEqual(list(self.accounts.glob("*.json")), [])
        self.assertFalse(self.credentials.exists())
        self.assertEqual(self.pending.read_text(), "Asia/Tokyo\n")
        self.assertEqual(self.pending.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.accounts.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("password", self.operations())
        self.assertNotIn("setup-surge", self.operations())
        self.assertNotIn("apply-surge", self.operations())
        self.assertIn("No accounts saved", self.fixture.cli("ls").stdout)
        before = self.snapshot()
        self.assertEqual(self.fixture.cli("timezone").stdout, "Asia/Tokyo\n")
        self.fixture.cli("timezone", "nonexistent", success=False)
        self.assertEqual(self.snapshot(), before)
        # Fake Claude receives the pending zone through the unchanged real hook.
        self.fixture.executable("claude", r'''#!/bin/bash
command jq -cn --args '{tz:env.TZ,browser:env.BROWSER,args:$ARGS.positional}' -- "$@" >> "$LAUNCHES"
[ "$1" = /login ] || exit 0
printf '{"claudeAiOauth":{"accessToken":"fixture-login"},"mcpOAuth":{"fixture":{"accessToken":"shared"}}}\n' > "$HOME/.claude/.credentials.json"
command jq '.oauthAccount={emailAddress:"fixture@example.invalid"}' "$HOME/.claude.json" > "$HOME/logged-in.json"
mv "$HOME/logged-in.json" "$HOME/.claude.json"
''')
        source = shlex.quote(str(self.home / ".ccacc-shell/hook.sh"))
        self.fixture.interactive("bash", f". {source}\nclaude /login\n", startup=False)
        launched = self.fixture.records(self.fixture.launches)[0]
        self.assertEqual(launched, {"tz": "Asia/Tokyo", "browser": "false", "args": ["/login"]})
        self.assertFalse(self.active.exists())
        self.run_body("main save personal")
        self.assertEqual(self.active.read_text(), "personal\n")
        self.assertEqual(self.fixture.cli("timezone", "personal").stdout, "Asia/Tokyo\n")
        self.assertFalse(self.pending.exists())
        self.assertEqual([p.name for p in self.accounts.glob("*.json")], ["personal.json"])
        backup = json.loads((self.accounts / "personal.credentials.enc").read_text())
        self.assertEqual(backup, {"claudeAiOauth": {"accessToken": "fixture-login"}})
        self.assertEqual(json.loads(self.credentials.read_text())["mcpOAuth"],
                         {"fixture": {"accessToken": "shared"}})
        self.assertEqual(self.hook_snapshot(), hook)

    def test_active_profile_is_saved_and_stashed_before_clean(self):
        self.saved_active()
        hook = self.hook_snapshot()
        policies = (self.accounts / "kept.surge").read_bytes()
        drafts = self.home / ".claude/feedback/drafts"
        drafts.mkdir(parents=True)
        (drafts / "draft.txt").write_text("saved feedback draft")
        self.run_body("main new --timezone Europe/London")
        self.assertFalse(self.active.exists())
        self.assertEqual([p.name for p in self.accounts.glob("*.json")], ["kept.json"])
        self.assertEqual(json.loads((self.accounts / "kept.json").read_text())["userID"], "old-id")
        self.assertEqual(json.loads((self.accounts / "kept.credentials.enc").read_text()),
                         {"claudeAiOauth": {"accessToken": "prior-backup"}})
        self.assertEqual((self.accounts / "kept/projects/history.json").read_text(), "fixture history")
        self.assertFalse((self.home / ".claude/projects").exists())
        self.assertEqual((self.accounts / "kept/feedback-drafts/draft.txt").read_text(), "saved feedback draft")
        self.assertEqual(json.loads(self.credentials.read_text()), self.shared)
        self.assertEqual((self.accounts / "kept.surge").read_bytes(), policies)
        self.assertEqual(self.fixture.cli("timezone", "kept").stdout, "UTC\n")
        self.assertEqual(self.fixture.cli("timezone").stdout, "Europe/London\n")
        self.assertEqual((self.home / ".claude/settings.json").read_text(), "shared settings")
        self.assertEqual(json.loads(self.config.read_text())["mcpServers"], {"shared": {}})
        operations = self.operations()
        self.assertLess(operations.index("stop"), operations.index("write"))
        self.assertNotIn("setup-surge", operations)
        self.assertNotIn("apply-surge", operations)
        self.assertEqual(self.hook_snapshot(), hook)

    def test_saved_account_uuid_can_prove_history_ownership_without_user_id(self):
        self.saved_active()
        config = json.loads(self.config.read_text())
        config.pop("userID")
        config["oauthAccount"]["accountUuid"] = "same-saved-account"
        self.config.write_text(json.dumps(config))
        (self.accounts / "kept.json").write_bytes(self.config.read_bytes())
        self.run_body("main new --timezone UTC")
        self.assertEqual((self.accounts / "kept/projects/history.json").read_text(), "fixture history")
        self.assertFalse(self.active.exists())

    def test_clean_preserves_meaningful_project_mcp_and_false_selections(self):
        self.saved_active()
        project_mcp = {
            "mcpServers": {"local": {"command": "fixture-server"}},
            "disabledMcpServers": ["disabled"],
            "enabledMcpServers": ["enabled"],
            "enabledMcpjsonServers": ["selected"],
            "disabledMcpjsonServers": ["unselected"],
            "enableAllProjectMcpServers": False,
        }
        config = json.loads(self.config.read_text())
        config["projects"] = {
            "/fixture/keep": {**project_mcp, "history": ["private prompt"], "hasTrustDialogAccepted": True},
            "/fixture/false": {"enableAllProjectMcpServers": False, "lastCost": 42},
            "/fixture/drop": {"mcpServers": {}, "enabledMcpServers": [], "hasTrustDialogAccepted": True},
            "/fixture/null": {"enableAllProjectMcpServers": None},
            "/fixture/malformed": "ignored malformed record",
        }
        self.config.write_text(json.dumps(config))
        (self.accounts / "kept.json").write_bytes(self.config.read_bytes())
        self.run_body("main new --timezone UTC")
        cleaned = json.loads(self.config.read_text())
        self.assertEqual(cleaned["projects"], {
            "/fixture/keep": project_mcp,
            "/fixture/false": {"enableAllProjectMcpServers": False},
        })
        self.assertEqual(cleaned["mcpServers"], {"shared": {}})
        self.assertEqual(json.loads(self.credentials.read_text()), self.shared)
        self.assertEqual(json.loads((self.accounts / "kept.json").read_text())["projects"], config["projects"])

    def test_confirmation_never_auto_saves_or_overwrites_saved_backups(self):
        self.saved_active()
        profiles = {p: p.read_bytes() for p in self.accounts.glob("kept.*")}
        self.run_body(r'''
save_account() { printf 'unexpected-save' >> "$SESSION_OPERATIONS"; return 98; }
ask_password() { printf 'unexpected-password' >> "$SESSION_OPERATIONS"; return 98; }
main new --timezone UTC
''')
        self.assertEqual({p: p.read_bytes() for p in profiles}, profiles)
        self.assertFalse(self.active.exists())
        self.assertNotIn("unexpected-save", self.operations())
        self.assertNotIn("unexpected-password", self.operations())

    def test_cancellation_precedes_credentials_prompts_processes_and_writes(self):
        self.saved_active()
        before = self.snapshot()
        for answer in ("", "\n", "n\n", "no\n", "YES\n", "sure\n"):
            result = self.run_body("main new --timezone UTC", success=False, stdin=answer)
            self.assertIn("Cleanup cancelled", result.stderr)
            self.assertNotIn("Choose timezone", result.stderr)
            self.assertEqual(self.operations(), [])
            self.assertEqual(self.snapshot(), before)

    def test_confirmed_unsaved_login_is_discarded_without_named_profile(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text("{}")
        self.login()
        self.run_body("main new --timezone UTC", stdin="Y\n")
        self.assertFalse(self.active.exists())
        self.assertEqual(list(self.accounts.glob("*.json")), [])
        self.assertEqual(json.loads(self.credentials.read_text()), self.shared)

    def test_mismatched_active_identity_never_overwrites_profile_history(self):
        self.saved_active()
        config = json.loads(self.config.read_text())
        config["userID"] = "different-unsaved-login"
        self.config.write_text(json.dumps(config))
        stash = self.accounts / "kept/projects"
        stash.mkdir(parents=True)
        (stash / "saved.txt").write_text("existing saved history")
        profile = (self.accounts / "kept.json").read_bytes()
        self.run_body("main new --timezone UTC")
        self.assertEqual((stash / "saved.txt").read_text(), "existing saved history")
        self.assertFalse((stash / "history.json").exists())
        self.assertEqual((self.accounts / "kept.json").read_bytes(), profile)
        self.assertFalse((self.home / ".claude/projects").exists())

    def test_loggedout_remnants_are_cleaned_and_repeat_clean_reuses_zone(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text('{"projects":{"old":{}},"mcpServers":{"shared":{}}}')
        remnants = self.home / ".claude/projects"
        remnants.mkdir()
        (remnants / "unowned.txt").write_text("retain this unowned history")
        self.run_body("main new", stdin="y\n8\nEurope/Paris\n")
        self.assertEqual(self.pending.read_text(), "Europe/Paris\n")
        self.assertFalse(remnants.exists())
        hook = self.hook_snapshot()
        self.run_body("main new")
        self.assertEqual(self.hook_snapshot(), hook)
        self.run_body("main new --timezone Asia/Shanghai")
        self.assertEqual(self.fixture.cli("timezone").stdout, "Asia/Shanghai\n")
        self.assertEqual([p.name for p in self.accounts.glob("*.json")], [])

    def test_invalid_new_arguments_and_absent_install_never_mutate(self):
        before = self.snapshot()
        for command in ("new old-name", "new ''", "new --alias alias", "new --timezone", "new --timezone UTC --timezone UTC",
                        "new --timezone Invalid/Zone", "new old-name --timezone UTC"):
            self.run_body("main " + command, success=False)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.operations(), [])
        (self.home / ".ccacc-shell/hook.sh").unlink()
        before = self.snapshot()
        result = self.run_body("new_command", success=False)
        self.assertIn("ccacc install", result.stderr)
        self.assertNotIn("Choose timezone", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.operations(), [])

    def test_pending_getter_and_launch_fail_closed_without_writes(self):
        self.run_body("main new --timezone UTC")
        source = shlex.quote(str(self.home / ".ccacc-shell/hook.sh"))
        for content in ("", "Invalid/Zone\n", "UTC\nAsia/Tokyo\n", "UTC\n\n"):
            self.pending.write_text(content)
            before = self.snapshot()
            self.assertEqual(self.fixture.cli("timezone", success=False).stdout, "")
            result = self.fixture.interactive("bash", f". {source}\nclaude /login\nexit 0\n", startup=False)
            self.assertIn("timezone", result.stderr)
            self.assertFalse(self.fixture.launches.exists())
            self.assertEqual(self.snapshot(), before)

    def test_saved_zone_wins_pending_and_activation_failure_keeps_pending(self):
        self.run_body("main new --timezone Asia/Tokyo")
        self.login()
        self.fixture.cli("timezone", "chosen", "UTC")
        self.run_body('mkdir "$HOME/activation-directory"; ACTIVE_FILE="$HOME/activation-directory"; main save chosen', success=False)
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.active.exists())
        self.assertTrue((self.accounts / "chosen.credentials.enc").exists())
        self.run_body("main save chosen")
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.fixture.cli("timezone").stdout, "UTC\n")

    def test_active_zone_is_not_inferred_for_new(self):
        self.saved_active()
        self.run_body("main new", stdin="yes\n3\n")
        self.assertEqual(self.fixture.cli("timezone").stdout, "Asia/Tokyo\n")
        self.assertEqual(self.fixture.cli("timezone", "kept").stdout, "UTC\n")
        self.assertFalse(self.active.exists())

    def test_stop_and_stash_conflicts_do_not_clear_login(self):
        self.saved_active()
        before = self.snapshot()
        self.run_body("stop_claude() { return 1; }; main new --timezone UTC", success=False)
        self.assertEqual(self.snapshot(), before)
        stash = self.accounts / "kept/projects"
        stash.mkdir(parents=True)
        (stash / "previous.txt").write_text("must not overwrite")
        before = self.snapshot()
        self.run_body("main new --timezone UTC", success=False)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.pending.exists())

    def test_readonly_config_and_accounts_fail_before_cleanup(self):
        self.saved_active()
        for path, mode in ((self.config, 0o444), (self.credentials, 0o444), (self.accounts, 0o555)):
            old_mode = path.stat().st_mode & 0o777
            path.chmod(mode)
            try:
                before = self.snapshot()
                result = self.run_body("main new --timezone UTC", success=False)
                self.assertNotIn("Cleaned current account", result.stdout)
                self.assertEqual(self.snapshot(), before)
                self.assertNotIn("stop", self.operations())
                self.assertNotIn("write", self.operations())
            finally:
                path.chmod(old_mode)

    def test_readonly_stash_parent_fails_before_cleanup(self):
        self.saved_active()
        stash = self.accounts / "kept"
        stash.mkdir()
        stash.chmod(0o555)
        try:
            before = self.snapshot()
            self.run_body("main new --timezone UTC", success=False)
            self.assertEqual(self.snapshot(), before)
            self.assertNotIn("stop", self.operations())
        finally:
            stash.chmod(0o700)

    def test_reset_copy_failure_is_propagated(self):
        self.saved_active()
        before = self.config.read_bytes()
        self.run_body('cp() { return 1; }; if reset_account_state; then exit 99; fi')
        self.assertEqual(self.config.read_bytes(), before)

    def test_late_cleanup_failures_restore_history_credentials_and_markers(self):
        self.saved_active()
        config = self.config.read_bytes()
        credentials = self.credentials.read_bytes()
        profiles = {p: p.read_bytes() for p in self.accounts.glob("kept.*")}
        recovery_copy = self.home / ".claude_backups/claude_dir_20261031_010101/history"
        recovery_copy.parent.mkdir(parents=True)
        recovery_copy.write_text("existing cleaner recovery history")
        failures = (
            'set_pending_timezone() { return 1; }',
            'reset_account_state() { printf "{}" > "$CLAUDE_JSON"; return 1; }',
            # The real reset helper runs under !, where Bash disables errexit.
            'cp() { if [ "$1" != -p ] && [ "$2" = "$CLAUDE_JSON" ]; then return 1; fi; command cp "$@"; }',
            # Pending was already replaced when the last metadata write fails.
            'rm() { if [ "$2" = "$ACTIVE_FILE" ]; then return 1; fi; command rm "$@"; }',
            # A failed write can have changed the store; rollback must still run.
            'write_credentials() { printf "%s\\n" "$1" > "$CREDENTIALS_FILE"; '
            '[ "$1" != "$(printf "%s" "$1" | jq -c "del(.claudeAiOauth)")" ]; }',
        )
        for failure in failures:
            with self.subTest(failure=failure):
                result = self.run_body(failure + '; main new --timezone UTC', success=False)
                self.assertIn("restored", result.stderr)
                self.assertNotIn("Cleaned current account", result.stdout)
                self.assertEqual(self.config.read_bytes(), config)
                self.assertEqual(self.credentials.read_bytes(), credentials)
                self.assertEqual(self.active.read_text(), "kept\n")
                self.assertFalse(self.pending.exists())
                self.assertEqual((self.home / ".claude/projects/history.json").read_text(), "fixture history")
                self.assertFalse((self.accounts / "kept/projects").exists())
                self.assertEqual({p: p.read_bytes() for p in profiles}, profiles)
                self.assertFalse((self.accounts / ".cleanup-recovery").exists())
                self.assertEqual(recovery_copy.read_text(), "existing cleaner recovery history")

    def test_partial_history_move_is_rolled_back_without_overwriting_stashes(self):
        self.saved_active()
        history = self.home / ".claude/history.jsonl"
        history.write_text("another history")
        config = self.config.read_bytes()
        self.run_body(r'''
mv() {
    if [ "$1" = "$CONFIG_DIR/history.jsonl" ]; then return 1; fi
    command mv "$@"
}
main new --timezone UTC
''', success=False)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(history.read_text(), "another history")
        self.assertEqual((self.home / ".claude/projects/history.json").read_text(), "fixture history")
        self.assertEqual(self.active.read_text(), "kept\n")
        self.assertFalse((self.accounts / ".cleanup-recovery").exists())

    def test_failed_rollback_retains_history_and_blocks_same_active_fastpath(self):
        self.saved_active()
        result = self.run_body(r'''
reset_account_state() { printf '{}' > "$CLAUDE_JSON"; return 1; }
mv() {
    case "$1" in */.cleanup-recovery/data/*) return 1 ;; esac
    command mv "$@"
}
main new --timezone UTC
''', success=False)
        recovery = self.accounts / ".cleanup-recovery"
        self.assertTrue(recovery.is_dir())
        self.assertIn(str(recovery), result.stderr)
        self.assertIn("fixture history", "".join(p.read_text() for p in recovery.rglob("*") if p.is_file()))
        self.assertNotIn("fixture-login", "".join(p.read_text() for p in recovery.rglob("*") if p.is_file()))
        self.assertEqual(recovery.stat().st_mode & 0o777, 0o700)
        self.assertEqual((recovery / "manifest.json").stat().st_mode & 0o777, 0o600)
        before = self.snapshot()
        result = self.run_body("main use kept", success=False)
        self.assertNotIn("already active", result.stdout)
        self.assertIn("incomplete", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_failed_move_that_created_destination_restores_history(self):
        self.saved_active()
        self.run_body(r'''
mv() {
    command mv "$@" || return 1
    [ "$1" != "$CONFIG_DIR/projects" ]
}
main new --timezone UTC
''', success=False)
        self.assertEqual((self.home / ".claude/projects/history.json").read_text(), "fixture history")
        self.assertEqual(self.active.read_text(), "kept\n")
        self.assertFalse((self.accounts / ".cleanup-recovery").exists())

    def test_keychain_rollback_failure_retains_no_secret_and_blocks_mutation(self):
        self.saved_active()
        keychain = self.fixture.fixture / "keychain.json"
        keychain.write_text(json.dumps({**self.shared, "claudeAiOauth": {"accessToken": "keychain-only-secret"}}))
        self.fixture.env["SESSION_KEYCHAIN"] = str(keychain)
        self.run_body(r'''
read_credentials() { cat "$SESSION_KEYCHAIN"; }
write_credentials() {
    if printf '%s' "$1" | jq -e 'has("claudeAiOauth")' >/dev/null; then return 1; fi
    printf '%s' "$1" > "$SESSION_KEYCHAIN"
}
reset_account_state() { return 1; }
main new --timezone UTC
''', success=False)
        recovery = self.accounts / ".cleanup-recovery"
        self.assertTrue(recovery.is_dir())
        self.assertNotIn("keychain-only-secret", "".join(p.read_text() for p in recovery.rglob("*") if p.is_file()))
        self.assertNotIn("claudeAiOauth", json.loads(keychain.read_text()))
        for command in ("main use kept", "main save kept", "main new --timezone UTC"):
            before = self.snapshot()
            self.run_body(command, success=False)
            self.assertEqual(self.snapshot(), before)

    def test_failed_new_restores_existing_pending_session(self):
        self.run_body("main new --timezone Asia/Tokyo")
        self.login("pending@example.invalid")
        credentials = self.credentials.read_bytes()
        config = self.config.read_bytes()
        self.run_body("set_pending_timezone() { return 1; }; main new --timezone UTC", success=False)
        self.assertEqual(self.pending.read_text(), "Asia/Tokyo\n")
        self.assertEqual(self.credentials.read_bytes(), credentials)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertFalse(self.active.exists())
        self.assertFalse((self.accounts / ".cleanup-recovery").exists())

    def test_new_loggedout_session_can_restore_saved_profile(self):
        self.saved_active()
        self.run_body("main new --timezone Asia/Tokyo")
        profiles = {p: p.read_bytes() for p in self.accounts.glob("kept.*")}
        result = self.run_body("main use kept")
        self.assertIn("Switched", result.stdout)
        self.assertEqual(self.active.read_text(), "kept\n")
        self.assertFalse(self.pending.exists())
        self.assertEqual({p: p.read_bytes() for p in profiles}, profiles)
        self.assertEqual([p.name for p in self.accounts.glob("*.json")], ["kept.json"])
        self.assertEqual((self.home / ".claude/projects/history.json").read_text(), "fixture history")
        self.assertEqual(json.loads(self.credentials.read_text())["claudeAiOauth"], {"accessToken": "prior-backup"})
        self.assertEqual(json.loads(self.credentials.read_text())["mcpOAuth"], self.shared["mcpOAuth"])

    def test_new_unsaved_login_refuses_use_without_mutation(self):
        self.saved_active()
        self.run_body("main new --timezone Asia/Tokyo")
        self.login("unsaved@example.invalid")
        before = self.snapshot()
        operations = self.operations()
        result = self.run_body("main use kept", success=False)
        self.assertIn("save", result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.operations(), operations + ["read"])

    def test_pending_restore_config_write_failure_cannot_claim_success(self):
        self.saved_active()
        self.run_body("main new --timezone Asia/Tokyo")
        config = self.config.read_bytes()
        result = self.run_body('cp() { return 1; }; main use kept', success=False)
        self.assertIn("Failed to restore", result.stdout + result.stderr)
        self.assertNotIn("Switched", result.stdout)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertFalse(self.active.exists())
        self.assertEqual(self.pending.read_text(), "Asia/Tokyo\n")
        self.assertEqual((self.accounts / "kept/projects/history.json").read_text(), "fixture history")

    def test_pending_keychain_login_and_unowned_history_refuse_use(self):
        self.saved_active()
        self.run_body("main new --timezone Asia/Tokyo")
        before = self.snapshot()
        self.run_body('read_credentials() { printf \'{"claudeAiOauth":{"accessToken":"unsaved-keychain"}}\'; }; main use kept', success=False)
        self.assertEqual(self.snapshot(), before)
        history = self.home / ".claude/projects"
        history.mkdir()
        (history / "unsaved").write_text("must save first")
        before = self.snapshot()
        self.run_body("main use kept", success=False)
        self.assertEqual(self.snapshot(), before)

    def test_full_cleanup_whitelist_preserves_saved_and_shared_assets(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text(json.dumps({"userID": "unsaved", "oauthAccount": {"emailAddress": "discard"},
            "cachedExperimentFeatures": {"old": True}, "seenNotifications": [1],
            "mcpServers": {"shared": {}}, "settings": {"keep": True}}))
        self.login()
        for name in ("backups/.hidden", "file-history/old", "feedback/drafts/draft", "projects/old", "todos/old",
                     "shell-snapshots/old", "statsig/old", "debug/old", "plans/old", "paste-cache/old",
                     "telemetry/old", "session-env/old", "tasks/old", "sessions/old", "ide/old", "daemon/old",
                     "jobs/old", "cache/old", "history.jsonl", "stats-cache.json", "remote-settings.json",
                     "policy-limits.json", "policy-limits.json.stamp.json", "plugins/plugin-directory-cache-v2.json",
                     "plugins/plugin-catalog-cache.json"):
            path = self.home / ".claude" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("discard fixture residue")
        preserved = {}
        for name in ("settings.json", "settings.local.json", "skills/fixture/SKILL.md", "plugins/installed/fixture",
                     "knowledge/keep.md", ".device-keys.json", "commands/keep.md", "mcp-oauth-state.json",
                     "accounts/saved.json", "accounts/saved.credentials.enc", "accounts/saved.surge",
                     "accounts/.timezones/saved", "accounts/.aliases/saved.json", "accounts/.check.enc"):
            path = self.home / ".claude" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("preserve fixture shared/profile asset")
            preserved[path] = path.read_bytes()
        other = self.home / "other-config/.credentials.json"
        other.parent.mkdir()
        other.write_text("unrelated credentials")
        preserved[other] = other.read_bytes()
        desktop = self.home / "Library/Application Support/Claude/keep"
        desktop.parent.mkdir(parents=True)
        desktop.write_text("desktop fixture")
        preserved[desktop] = desktop.read_bytes()
        recovery = self.home / ".claude_backups"
        recovery.mkdir()
        (recovery / "claude_.claude.json_20261031_010101").write_text("recognized account recovery")
        (recovery / "claude_dir_20261031_010101").mkdir()
        (recovery / "claude_dir_20261031_010101/secret").write_text("recognized account recovery")
        unrelated = recovery / "keep-manual-backup"
        unrelated.write_text("unrecognized backup")
        preserved[unrelated] = unrelated.read_bytes()
        hook = self.hook_snapshot()
        self.run_body("main new --timezone UTC")
        self.assertFalse((recovery / "claude_.claude.json_20261031_010101").exists())
        self.assertFalse((recovery / "claude_dir_20261031_010101").exists())
        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)
        self.assertEqual(self.hook_snapshot(), hook)
        for name in ("sessions", "ide", "daemon", "jobs", "cache", "backups", "feedback/drafts", "history.jsonl",
                     "remote-settings.json", "policy-limits.json", "plugins/plugin-catalog-cache.json"):
            self.assertFalse((self.home / ".claude" / name).exists(), name)
        self.assertEqual(json.loads(self.credentials.read_text()), self.shared)
        clean = json.loads(self.config.read_text())
        self.assertNotIn("cachedExperimentFeatures", clean)
        self.assertNotIn("seenNotifications", clean)
        self.assertEqual(clean["mcpServers"], {"shared": {}})

    def test_optional_malformed_file_is_refused_before_default_store_mutation(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text("{}")
        for content in (b"", b"[]", b"{} {}", b'{"claudeAiOauth":"bad"}',
                        b'{"claudeAiOauth":{"accessToken":"fixture"}}\x00'):
            self.credentials.write_bytes(content)
            before = self.snapshot()
            result = self.run_body("main new --timezone UTC", success=False)
            self.assertIn("Malformed optional credentials", result.stderr)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.operations(), [])
        self.credentials.write_bytes(b"")
        self.run_body("cleanup_optional_credentials_file", success=False)
        self.assertEqual(self.credentials.read_bytes(), b"")

    def test_default_keychain_and_optional_file_clear_only_login_fields(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text("{}")
        self.login()
        keychain = self.fixture.fixture / "keychain.json"
        keychain.write_text(json.dumps({**self.shared, "claudeAiOauth": {"accessToken": "keychain-fixture"}}))
        self.fixture.env["SESSION_KEYCHAIN"] = str(keychain)
        self.run_body(r'''
read_credentials() { cat "$SESSION_KEYCHAIN"; }
write_credentials() { printf '%s' "$1" > "$SESSION_KEYCHAIN"; }
delete_credentials() { rm -f "$SESSION_KEYCHAIN"; }
main new --timezone UTC
''')
        self.assertEqual(json.loads(keychain.read_text()), self.shared)
        self.assertEqual(json.loads(self.credentials.read_text()), self.shared)

    def test_symlinked_cleanup_and_ancestor_paths_fail_without_mutation(self):
        self.accounts.mkdir(parents=True)
        self.config.write_text("{}")
        outside = self.fixture.fixture / "outside"
        outside.mkdir()
        (outside / "keep").write_text("never touch")
        for name in ("sessions", "plugins", "feedback"):
            target = self.home / ".claude" / name
            target.symlink_to(outside, target_is_directory=True)
            before = self.snapshot()
            self.run_body("main new --timezone UTC", success=False)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual((outside / "keep").read_text(), "never touch")
            self.assertNotIn("stop", self.operations())
            target.unlink()

    def test_actual_runtime_guard_refuses_live_unverifiable_and_bridge_owners(self):
        self.run_body("main new --timezone UTC")
        runtime = self.home / ".claude/sessions"
        runtime.mkdir()
        (runtime / "keep").write_text("runtime fixture")
        for process_body in ("return 1", "printf '321 /bin/claude claude\\n'",
                             "printf 'not-a-valid-process-list\\n'"):
            before = self.snapshot()
            self.run_body(f"claude_process_ids() {{ :; }}; ps() {{ {process_body}; }}; main new --timezone UTC", success=False, process_mocks=False)
            self.assertEqual(self.snapshot(), before)
        # Snapshot has no Claude executable, but a live recorded PID still owns
        # runtime state and must block deletion without being signaled.
        config = json.loads(self.config.read_text())
        config["replBridgePlaceholders"] = {"session_fixture": {"pid": 321}}
        self.config.write_text(json.dumps(config))
        before = self.snapshot()
        self.run_body("claude_process_ids() { :; }; ps() { printf '321 /bin/other other\\n'; }; main new --timezone UTC",
                      success=False, process_mocks=False)
        self.assertEqual(self.snapshot(), before)

    def test_cleanup_never_signals_pid_one(self):
        self.run_body(r'''
source "$FIXTURE_CORE" help >/dev/null
claude_process_ids() { printf '1\n'; }
ps() { printf '1\n'; }
kill() { printf '%s\n' "$*" >> "$SESSION_OPERATIONS"; }
cleanup_stop_processes
''', success=False, process_mocks=False)
        self.assertEqual(self.operations(), [])

    def test_ancestor_check_covers_refreshed_signal_candidates(self):
        self.run_body(r'''
source "$FIXTURE_CORE" help >/dev/null
printf 0 > "$HOME/process-pass"
claude_process_ids() {
    local count=$(cat "$HOME/process-pass")
    printf '%s' "$((count+1))" > "$HOME/process-pass"
    if [ "$count" -eq 0 ]; then printf '101\n'; else printf '%s\n' "$$"; fi
}
ps() { printf '1\n'; }
kill() { printf '%s\n' "$*" >> "$SESSION_OPERATIONS"; }
sleep() { :; }
cleanup_stop_processes
''', success=False, process_mocks=False)
        self.assertEqual(self.operations(), ["-TERM 101"])

    def test_pending_is_not_a_named_timezone_and_survives_failed_save(self):
        self.run_body("main new --timezone Australia/Sydney")
        self.login()
        self.run_body("save_account() { return 1; }; main save intended", success=False)
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.active.exists())
        self.assertFalse((self.accounts / "intended.json").exists())
        self.fixture.cli("timezone", "missing", success=False)
        self.run_body("main save intended --timezone UTC")
        self.assertFalse(self.pending.exists())
        # A pending record cannot override an active profile, even if the named
        # timezone is corrupt or absent.
        self.pending.write_text("Asia/Tokyo\n")
        self.assertEqual(self.fixture.cli("timezone").stdout, "UTC\n")
        (self.accounts / ".timezones/intended").unlink()
        self.assertEqual(self.fixture.cli("timezone", success=False).stdout, "")


if __name__ == "__main__":
    unittest.main()
