"""Plain login backups and the one-time migration of old encrypted backups (real openssl)."""

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "ccacc").read_text().rsplit('main "$@"', 1)[0]
PASSWORD = "fixture-master"
LOGIN = {"claudeAiOauth": {"accessToken": "fixture-token", "refreshToken": "fixture-refresh"}}


class BackupMigrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".orca").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".orca", prefix="backup-migration-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / "home"
        self.accounts = self.home / ".claude" / "accounts"
        self.accounts.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.fixture))
        for name in ("BASH_ENV", "ENV"):
            self.env.pop(name, None)

    def encrypt(self, path, text, password=PASSWORD):
        # Same parameters as the encrypted backups written by earlier ccacc versions.
        subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-md", "sha256",
                        "-salt", "-pass", "env:FIXTURE_PASSWORD", "-out", str(path)],
                       input=text.encode(), env=dict(self.env, FIXTURE_PASSWORD=password), check=True)
        path.chmod(0o600)

    def legacy(self, *names, check=True):
        if check:
            self.encrypt(self.accounts / ".check.enc", "ccacc")
        for name in names:
            login = {"claudeAiOauth": {"accessToken": "fixture-" + name}}
            self.encrypt(self.accounts / (name + ".credentials.enc"), json.dumps(login))

    def run_body(self, body, stdin="", success=True):
        result = subprocess.run(["/bin/bash", "-c", SOURCE + "\n" + body], env=self.env, input=stdin,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        return result

    def snapshot(self):
        return {path.name: path.read_bytes() for path in sorted(self.accounts.iterdir())}

    def test_backups_are_plain_private_files_with_only_the_claude_login(self):
        secret = dict(LOGIN, mcpOAuth={"shared": {"accessToken": "fixture-shared"}})
        self.env["SECRET"] = json.dumps(secret)
        self.run_body('write_backup work credentials "$SECRET"\nread_backup work credentials > "$TMPDIR/out"')
        backup = self.accounts / "work.credentials"
        self.assertEqual(json.loads(backup.read_text()), LOGIN)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(json.loads((self.fixture / "out").read_text()), LOGIN)
        self.assertEqual(sorted(p.name for p in self.accounts.iterdir()), ["work.credentials"])
        # A logged-out account removes the backup instead of writing an empty one.
        self.env["SECRET"] = json.dumps({"mcpOAuth": {}})
        self.run_body('write_backup work credentials "$SECRET"')
        self.assertFalse(backup.exists())

    def test_migration_converts_every_backup_then_removes_encrypted_files(self):
        self.legacy("alpha", "beta")
        result = self.run_body("migrate_encrypted_backups", stdin=PASSWORD + "\n")
        self.assertEqual(sorted(p.name for p in self.accounts.iterdir()), ["alpha.credentials", "beta.credentials"])
        for name in ("alpha", "beta"):
            backup = self.accounts / (name + ".credentials")
            self.assertEqual(json.loads(backup.read_text()), {"claudeAiOauth": {"accessToken": "fixture-" + name}})
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertIn("Converted 2 login backup(s)", result.stderr)
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        # Nothing is left to migrate: no prompt and stdin stays unread.
        result = self.run_body('migrate_encrypted_backups\nIFS= read -r rest; printf "%s" "$rest"', stdin="untouched\n")
        self.assertEqual(result.stdout, "untouched")
        self.assertNotIn("master password", result.stderr)

    def test_migration_without_check_file_validates_by_decrypting(self):
        self.legacy("alpha", check=False)
        self.run_body("migrate_encrypted_backups", stdin=PASSWORD + "\n")
        self.assertEqual(sorted(p.name for p in self.accounts.iterdir()), ["alpha.credentials"])

    def test_wrong_or_missing_password_changes_nothing(self):
        for check in (True, False):
            for stdin in ("wrong-password\n", ""):
                with self.subTest(check=check, stdin=stdin):
                    for path in self.accounts.iterdir():
                        path.unlink()
                    self.legacy("alpha", "beta", check=check)
                    before = self.snapshot()
                    self.run_body("migrate_encrypted_backups", stdin=stdin, success=False)
                    self.assertEqual(self.snapshot(), before)

    def test_interrupted_migration_resumes_and_conflicts_are_refused(self):
        self.legacy("alpha", "beta")
        (self.accounts / "alpha.credentials").write_text(json.dumps({"claudeAiOauth": {"accessToken": "fixture-alpha"}}))
        self.run_body("migrate_encrypted_backups", stdin=PASSWORD + "\n")
        self.assertEqual(sorted(p.name for p in self.accounts.iterdir()), ["alpha.credentials", "beta.credentials"])

        self.legacy("gamma")
        (self.accounts / "gamma.credentials").write_text('{"claudeAiOauth":{"accessToken":"different"}}')
        before = self.snapshot()
        result = self.run_body("migrate_encrypted_backups", stdin=PASSWORD + "\n", success=False)
        self.assertIn("differ", result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_leftover_check_file_is_removed_without_a_prompt(self):
        self.encrypt(self.accounts / ".check.enc", "ccacc")
        self.run_body("migrate_encrypted_backups", stdin="")
        self.assertEqual(list(self.accounts.iterdir()), [])

    def test_profiles_with_only_a_legacy_backup_still_count(self):
        self.legacy("alpha", check=False)
        self.run_body("profile_exists alpha")
        self.run_body("profile_exists beta", success=False)


if __name__ == "__main__":
    unittest.main()
