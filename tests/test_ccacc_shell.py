"""Isolated shell-hook contract tests. Run: uv run --no-project python -m unittest discover -s tests."""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "ccacc_shell.sh"


class ShellHookTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".orca" / "shell-hook-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="home space's ", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.bin = self.home / "bin space's"
        self.bin.mkdir()
        self.log = self.home / "claude.calls"
        self.probe = self.home / "probe.json"
        self.ccacc = self.home / "ccacc script's"
        self.zone = self.home / "active-zone"
        self.zone.write_text("Australia/Sydney\n")
        self.ccacc.write_text(
            '#!/bin/bash\n[ "$1" = timezone ] || exit 3\n'
            'printf "called\\n" >> "$HOME/timezone.calls"\n'
            '[ -s "$HOME/active-zone" ] || exit 4\ncat "$HOME/active-zone"\n'
        )
        # Intentionally non-executable, matching the repository's ccacc mode.
        self.ccacc.chmod(0o600)
        self.claude = self.bin / "claude"
        self.claude.write_text(
            '#!/bin/bash\n{ printf "BROWSER=%s\\nTZ=%s\\nKEEP=%s\\nWRAPPED=%s\\n" '
            '"$BROWSER" "$TZ" "$KEEP" "$WRAPPED"; '
            'printf "ARG=%s\\n" "$@"; } >> "$HOME/claude.calls"\n'
        )
        self.claude.chmod(0o700)
        self.env = {
            "HOME": str(self.home), "SHELL": "/bin/bash", "PATH": f"{self.bin}:/opt/homebrew/bin:/usr/bin:/bin",
            "LC_ALL": "C", "TMPDIR": str(self.home), "BASH_ENV": "/dev/null",
        }

    def shell(self, code, shell="bash", check=True):
        flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
        if shell == "bash":
            code = "shopt -s expand_aliases\n" + code
        result = subprocess.run([shutil.which(shell), *flags, "-c", code], env=self.env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def install(self, definition=None, shell="bash", check=True, raw_probe=None, zone="Australia/Sydney"):
        self.env["SHELL"] = f"/bin/{shell}"
        self.probe.write_text(raw_probe if raw_probe is not None else json.dumps(
            {"missing": True} if definition is None else {"definition": definition}
        ))
        return self.shell(
            f"set -e\nsource {shlex.quote(str(LIBRARY))}\n"
            'probe_alias() { [ "$1" = claude ] || return 1; cat "$HOME/probe.json"; }\n'
            'valid_timezone() { case "$1" in Australia/Sydney|Asia/Tokyo) return 0;; *) return 1;; esac; }\n'
            f"ccacc_install_shell_hook {shlex.quote(str(self.ccacc))} account {shlex.quote(zone)}\n",
            check=check,
        )

    @property
    def private(self):
        return self.home / ".ccacc-shell"

    def launch(self, shell="bash", check=True, args="'user arg' '$(touch should-not-exist)'", prefix=""):
        # eval is only the test caller's second parse, necessary because aliases
        # are expanded when a command is read, before source in the same -c.
        return self.shell(prefix + '\nsource "$HOME/.ccacc-shell/hook.sh"\n'
                          + f"eval {shlex.quote('claude ' + args)}\n", shell, check)

    def test_missing_alias_dynamic_timezone_and_no_source_execution(self):
        out = self.install()
        self.assertIn("source '", out.stdout)
        self.assertFalse((self.private / "original-alias.json").exists())
        self.assertFalse(self.log.exists())
        self.assertFalse((self.home / "timezone.calls").exists())
        self.install("__ccacc_launch")
        self.assertFalse((self.private / "original-alias.json").exists())
        for shell in ("bash", "zsh"):
            self.shell('source "$HOME/.ccacc-shell/hook.sh"', shell)
        self.assertFalse((self.home / "timezone.calls").exists())
        self.launch()
        self.zone.write_text("Asia/Tokyo\n")
        self.launch("zsh")
        calls = self.log.read_text()
        self.assertIn("TZ=Australia/Sydney", calls)
        self.assertIn("TZ=Asia/Tokyo", calls)
        self.assertEqual(calls.count("BROWSER=false"), 2)
        self.assertIn("ARG=user arg", calls)
        self.assertIn("ARG=$(touch should-not-exist)", calls)
        self.assertFalse((ROOT / "should-not-exist").exists())

    def test_literal_assignments_executable_flags_and_wrapper_retained(self):
        definition = f"KEEP='hello world' BROWSER=open TZ=Old _op_agent env WRAPPED=yes TZ=Bad BROWSER=yes {shlex.quote(str(self.claude))} --model 'opus 4' --literal '$HOME;safe'"
        self.install(definition)
        saved = json.loads((self.private / "original-alias.json").read_text())
        self.assertEqual(saved["definition"], definition)
        for shell in ("bash", "zsh"):
            self.launch(shell, prefix='_op_agent() { "$@"; }')
        calls = self.log.read_text()
        for expected in ("KEEP=hello world", "WRAPPED=yes", "ARG=--model", "ARG=opus 4", "ARG=$HOME;safe"):
            self.assertEqual(calls.count(expected), 2)
        self.assertEqual(calls.count("TZ=Australia/Sydney"), 2)
        self.assertNotIn("TZ=Bad", calls)

    def test_env_assignments_and_quoting(self):
        definition = f'env KEEP="a \\"quote\\"" TZ=Bad BROWSER=yes {shlex.quote(str(self.claude))} "arg with spaces"'
        self.install(definition)
        for shell in ("bash", "zsh"):
            self.launch(shell)
        self.assertEqual(self.log.read_text().count('KEEP=a "quote"'), 2)

    def test_missing_invalid_and_unknown_timezone_fail_closed(self):
        self.install()
        for value in ("", "No/Such_Zone\n", "Asia/Tokyo\nAustralia/Sydney\n", "../etc/passwd\n", "$(touch bad)\n"):
            self.zone.write_text(value)
            for shell in ("bash", "zsh"):
                result = self.launch(shell, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("timezone", result.stderr)
        self.assertFalse(self.log.exists())

    def test_overwrite_once_keeps_original_and_exact_rc_bytes_modes(self):
        rc = self.home / ".bashrc"
        content = b"# untouched\r\nexport X='caf\xc3\xa9'\n\n# final without newline"
        rc.write_bytes(content)
        rc.chmod(0o640)
        login = self.home / ".bash_profile"
        login.write_bytes(b"# login\n")
        self.install("KEEP=first claude --first")
        rc_once = rc.read_bytes()
        login_once = login.read_bytes()
        hook_once = (self.private / "hook.sh").read_bytes()
        original = (self.private / "original-alias.json").read_bytes()
        for path in (rc, login, self.private / "hook.sh"):
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        self.assertTrue(rc_once.startswith(content))
        self.install("__ccacc_launch")
        self.assertEqual(rc.read_bytes(), rc_once)
        self.assertEqual(login.read_bytes(), login_once)
        self.assertEqual((self.private / "hook.sh").read_bytes(), hook_once)
        self.assertEqual((self.private / "original-alias.json").read_bytes(), original)
        for path in (rc, login, self.private / "hook.sh"):
            self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)
        self.assertEqual(stat.S_IMODE(rc.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o700)
        for name in ("hook.sh", "managed.json", "original-alias.json"):
            self.assertEqual(stat.S_IMODE((self.private / name).stat().st_mode), 0o600)
        # Simulate a later user alias and show sourcing our end-of-rc block wins.
        self.launch(prefix="alias claude='false'")
        self.assertIn("KEEP=first", self.log.read_text())
        self.assertIn("ARG=--first", self.log.read_text())

    def test_managed_block_moves_to_end_without_changing_surrounding_bytes(self):
        self.install()
        rc = self.home / ".bashrc"
        before = rc.read_bytes()
        rc.write_bytes(before + b"# a later alias\nalias claude=false\n")
        self.install()
        after = rc.read_bytes()
        self.assertTrue(after.startswith(b"# a later alias\nalias claude=false\n"))
        self.assertEqual(after.count(b"# >>> ccacc shell hook >>>"), 1)
        self.assertTrue(after.endswith(b"# <<< ccacc shell hook <<<\n"))

    def test_bash_login_selection(self):
        for selected in (".profile", ".bash_login", ".bash_profile"):
            path = self.home / selected
            path.write_text(f"# {selected}\n")
            self.install()
            self.assertIn("ccacc shell hook", path.read_text())
            self.assertIn("ccacc shell hook", (self.home / ".bashrc").read_text())
        # Lower-priority files are left alone when a profile shadows them.
        for name in (".profile", ".bash_login"):
            path = self.home / name
            before = path.read_bytes()
            self.install()
            self.assertEqual(path.read_bytes(), before)

    def test_moving_block_preserves_line_boundary_and_rejects_duplicates(self):
        rc = self.home / ".bashrc"
        rc.write_text("export KEEP=original")
        self.install()
        block = rc.read_bytes()[len(b"export KEEP=original"):]
        rc.write_bytes(rc.read_bytes() + b"export LATER=added\n")
        self.install()
        self.assertTrue(rc.read_bytes().startswith(b"export KEEP=original\nexport LATER=added\n"))
        self.shell('source "$HOME/.bashrc"\n[ "$KEEP" = original ] && [ "$LATER" = added ]')
        rc.write_bytes(rc.read_bytes() + block)
        before = rc.read_bytes()
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(rc.read_bytes(), before)

    def test_zsh_custom_zdotdir(self):
        dot = self.home / "zsh dot's"
        dot.mkdir()
        self.env["ZDOTDIR"] = str(dot)
        rc = dot / ".zshrc"
        rc.write_bytes(b"# zsh\n")
        self.install(shell="zsh")
        self.assertTrue(rc.read_bytes().startswith(b"# zsh\n"))
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".bashrc").exists())
        self.launch("zsh")
        self.assertIn("BROWSER=false", self.log.read_text())

    def test_zsh_default_rc(self):
        self.install(shell="zsh")
        self.assertTrue((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".bashrc").exists())

    def test_unsupported_aliases_leave_rc_and_files_untouched(self):
        rc = self.home / ".bashrc"
        rc.write_text("# original\n")
        aliases = (
            "claude --flag; touch nope", "TZ=$(touch nope) claude", "env -i claude --arg",
            "unknown_wrapper claude --arg", "_op_agent --option env claude", "claude | cat",
            "claude > output", "KEEP=$HOME claude", "claude `touch nope`", "claude\n",
            "claude && false", "claude 'unterminated", "claude *.txt",
        )
        for definition in aliases:
            result = self.install(definition, check=False)
            self.assertNotEqual(result.returncode, 0, definition)
            self.assertIn("unsupported", result.stderr)
            self.assertNotIn(definition, result.stderr)
            self.assertEqual(rc.read_text(), "# original\n")
            self.assertFalse(self.private.exists())
        self.assertFalse((ROOT / "nope").exists())

    def test_symlink_and_malformed_targets_rejected(self):
        target = self.home / "original"
        target.write_text("do not touch\n")
        rc = self.home / ".bashrc"
        rc.symlink_to(target)
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual(target.read_text(), "do not touch\n")
        rc.unlink()
        rc.write_text("# >>> ccacc shell hook >>>\nmalformed\n")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse(self.private.exists())
        rc.write_bytes(b"# nul\x00data")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        rc.unlink()
        self.private.symlink_to(self.bin, target_is_directory=True)
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse((self.bin / "hook.sh").exists())

    def test_symlink_ancestor_and_hook_target_rejected(self):
        dot = self.home / "dot"
        dot.symlink_to(self.bin, target_is_directory=True)
        self.env["ZDOTDIR"] = str(dot)
        self.assertNotEqual(self.install(shell="zsh", check=False).returncode, 0)
        del self.env["ZDOTDIR"]
        self.install()
        hook = self.private / "hook.sh"
        hook.unlink()
        hook.symlink_to(self.ccacc)
        original = self.ccacc.read_bytes()
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual(self.ccacc.read_bytes(), original)

    def test_corrupt_private_record_and_probe_rejected(self):
        for probe in ('{"missing":false}', '{"definition":4}', '{"definition":"x\\u0000y"}',
                      '{"missing":true}\n{"missing":true}', '{"definition":"claude"}\n{"definition":"claude"}', 'invalid'):
            self.assertNotEqual(self.install(raw_probe=probe, check=False).returncode, 0)
            self.assertFalse(self.private.exists())
        self.install("claude --retained")
        (self.private / "managed.json").write_text("{}")
        before = (self.home / ".bashrc").read_bytes()
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual((self.home / ".bashrc").read_bytes(), before)

    def test_unknown_shell_missing_zdotdir_invalid_install_timezone(self):
        self.assertNotEqual(self.install(shell="fish", check=False).returncode, 0)
        self.assertFalse(self.private.exists())
        self.env["ZDOTDIR"] = str(self.home / "absent")
        self.assertNotEqual(self.install(shell="zsh", check=False).returncode, 0)
        self.assertNotEqual(self.install(zone="", check=False).returncode, 0)
        self.assertFalse(self.private.exists())


if __name__ == "__main__":
    unittest.main()
