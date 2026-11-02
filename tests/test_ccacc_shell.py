"""Isolated shell-hook contract tests. Run: uv run --no-project python -m unittest discover -s tests."""
import json
import os
import pty
import select
import time
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest

# save/use read the weekly reset from Keychain and network; fixtures forbid both.
os.environ["CCACC_NO_WEEKLY_RESET"] = "1"
# list/current/use refresh the latest Release from GitHub; fixtures forbid the network.
os.environ["CCACC_NO_UPDATE_CHECK"] = "1"

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
        self.ccacc = self.home / "ccacc script's"
        self.zone = self.home / "active-zone"
        self.zone.write_text("Australia/Sydney\n")
        self.ccacc.write_text(
            '#!/bin/bash\n[ "$1" = timezone ] || exit 3\n'
            'printf "called\\n" >> "$HOME/timezone.calls"\n'
            '[ ! -f "$HOME/unnamed" ] || exit 3\n'
            '[ -s "$HOME/active-zone" ] || exit 4\ncat "$HOME/active-zone"\n'
        )
        # Intentionally non-executable, matching the repository's ccacc mode.
        self.ccacc.chmod(0o600)
        self.claude = self.bin / "claude"
        self.claude.write_text(
            '#!/bin/bash\n{ printf "BROWSER=%s\\nTZ=%s\\nTZ_SET=%s\\nKEEP=%s\\nWRAPPED=%s\\n" '
            '"$BROWSER" "$TZ" "${TZ+x}" "$KEEP" "$WRAPPED"; '
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
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def install(self, definition=None, shell="bash", check=True):
        self.env["SHELL"] = f"/bin/{shell}"
        alias = "builtin alias " + shlex.quote("claude=" + definition) + "\n" if definition else ""
        return self.shell(
            f"set -e\n{alias}source {shlex.quote(str(LIBRARY))}\n"
            'probe_alias() { printf forbidden > "$HOME/probe.calls"; return 99; }\n'
            f"ccacc_install_shell_hook {shlex.quote(str(self.ccacc))}\n",
            check=check,
        )

    @property
    def private(self):
        return self.home / ".ccacc-shell"

    def test_preflight_validates_without_writing_or_launching(self):
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        result = self.shell(f"source {shlex.quote(str(LIBRARY))}\n"
                            f"ccacc_preflight_shell_hook {shlex.quote(str(self.ccacc))}\n")
        self.assertEqual(result.stdout, "")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse(self.private.exists())

    def test_read_only_startup_rejected_during_preflight(self):
        rc = self.home / ".bashrc"
        rc.write_text("# existing startup configuration\n")
        rc.chmod(0o444)
        if os.access(rc, os.W_OK):
            self.skipTest("Current user can write mode-0444 files")
        before = {p.relative_to(self.home): p.read_bytes()
                  for p in self.home.rglob("*") if p.is_file()}
        result = self.shell(
            f"source {shlex.quote(str(LIBRARY))}\n"
            f"ccacc_preflight_shell_hook {shlex.quote(str(self.ccacc))}\n",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, {p.relative_to(self.home): p.read_bytes()
                                  for p in self.home.rglob("*") if p.is_file()})
        self.assertFalse(self.private.exists())

    def launch(self, shell="bash", check=True, args="'user arg' '$(touch should-not-exist)'", prefix=""):
        return self.shell(prefix + '\nsource "$HOME/.ccacc-shell/hook.sh"\n'
                          + f"claude {args}\n", shell, check)

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

    def test_aliases_and_functions_do_not_recurse_or_supply_old_flags(self):
        self.install("KEEP=old _op_agent env claude --dangerously-skip-permissions --model old")
        self.assertFalse((self.private / "original-alias.json").exists())
        self.assertFalse((self.home / "probe.calls").exists())
        for shell in ("bash", "zsh"):
            self.launch(shell, prefix="function claude { return 99; }\nalias claude=false")
        calls = self.log.read_text()
        self.assertEqual(calls.count("BROWSER=false"), 2)
        self.assertNotIn("ARG=--model", calls)
        self.assertNotIn("dangerously", calls)
        self.assertNotIn("KEEP=old", calls)

    def test_missing_invalid_and_unknown_timezone_fail_closed(self):
        self.install()
        for value in ("", "No/Such_Zone\n", "Asia/Tokyo\nAustralia/Sydney\n", "../etc/passwd\n", "$(touch bad)\n"):
            self.zone.write_text(value)
            for shell in ("bash", "zsh"):
                result = self.launch(shell, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("timezone", result.stderr)
        self.assertFalse(self.log.exists())

    def test_unnamed_launch_unsets_inherited_timezone(self):
        self.install("TZ=Old _op_agent env TZ=Bad claude --model old")
        (self.home / "unnamed").touch()
        self.env["TZ"] = "America/New_York"
        for shell in ("bash", "zsh"):
            self.launch(shell, prefix='_op_agent() { return 99; }')
        calls = self.log.read_text()
        self.assertEqual(calls.count("TZ_SET=\n"), 2)
        self.assertEqual(calls.count("BROWSER=false"), 2)
        self.assertNotIn("ARG=--model", calls)

    def test_readonly_caller_environment_does_not_override_child_settings(self):
        self.install()
        for unnamed in (False, True):
            if unnamed:
                (self.home / "unnamed").touch()
            for shell in ("bash", "zsh"):
                self.launch(shell, prefix='export TZ=America/New_York BROWSER=open\nreadonly TZ BROWSER')
        calls = self.log.read_text()
        self.assertEqual(calls.count("TZ=Australia/Sydney\n"), 2)
        self.assertEqual(calls.count("TZ_SET=\n"), 2)
        self.assertEqual(calls.count("BROWSER=false"), 4)

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
        original = b"legacy fixture backup\n"
        (self.private / "original-alias.json").write_bytes(original)
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
        for name in ("hook.sh", "managed.json"):
            self.assertEqual(stat.S_IMODE((self.private / name).stat().st_mode), 0o600)
        # Simulate a later user alias and show sourcing our end-of-rc block wins.
        self.launch(prefix="alias claude='false'")
        self.assertNotIn("KEEP=first", self.log.read_text())
        self.assertNotIn("ARG=--first", self.log.read_text())

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
        self.assertTrue((self.home / ".zlogin").exists())
        self.assertFalse((self.home / ".bashrc").exists())

    def test_zsh_effective_startup_directory_overrides_environment_and_refreshes(self):
        inherited = self.home / "inherited directory"
        inherited.mkdir()
        self.env["ZDOTDIR"] = str(inherited)
        for name in ("effective first", "effective second"):
            dot = self.home / name
            dot.mkdir()
            (inherited / ".zshenv").write_text("ZDOTDIR=" + shlex.quote(str(dot)) + "\n")
            self.install(shell="zsh")
            for filename in (".zshrc", ".zlogin"):
                self.assertTrue((dot / filename).exists())
                self.assertFalse((inherited / filename).exists())
        self.assertFalse((self.home / "probe.calls").exists())

    def test_zsh_login_override_repeated_source_and_unchanged_files(self):
        dot = self.home / "login dot's"
        dot.mkdir()
        self.env["ZDOTDIR"] = str(dot)
        prefixes = {
            ".zshrc": b"# zsh caf\xc3\xa9\nexport UNRELATED=retained",
            ".zlogin": b"# later login alias\nalias claude='BROWSER=open TZ=Old claude --bad-login'\n",
        }
        for filename, content in prefixes.items():
            path = dot / filename
            path.write_bytes(content)
            path.chmod(0o640)
        self.install("KEEP=original claude --retained", shell="zsh")
        snapshots = {}
        for filename, content in prefixes.items():
            path = dot / filename
            self.assertTrue(path.read_bytes().startswith(content))
            self.assertEqual(path.read_bytes().count(b"# >>> ccacc shell hook >>>"), 1)
            self.assertTrue(path.read_bytes().endswith(b"# <<< ccacc shell hook <<<\n"))
            os.utime(path, ns=(2_000_000_000, 2_000_000_000))
            snapshots[path] = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
        self.install("__ccacc_launch", shell="zsh")
        for path, (content, mtime, inode) in snapshots.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        source = 'source "$ZDOTDIR/.zshrc"\nsource "$ZDOTDIR/.zlogin"\n' * 2
        self.shell(source, "zsh")
        self.assertFalse(self.log.exists())
        self.assertFalse((self.home / "timezone.calls").exists())
        self.zone.write_text("Asia/Tokyo\n")
        self.shell(source + "eval 'claude --login'\n", "zsh")
        calls = self.log.read_text()
        for expected in ("BROWSER=false", "TZ=Asia/Tokyo", "ARG=--login"):
            self.assertEqual(calls.count(expected), 1)
        self.assertNotIn("--bad-login", calls)

    def test_zsh_unexported_zdotdir_real_fixture_login(self):
        dot = self.home / "unexported dot's"
        dot.mkdir()
        (self.home / ".zshenv").write_text("ZDOTDIR=" + shlex.quote(str(dot)) + "\n")
        (dot / ".zshrc").write_text("# isolated interactive fixture\n")
        (dot / ".zlogin").write_text("alias claude='BROWSER=open TZ=Old claude --bad-login'\n")
        # -d skips optional global rc files. The isolated HOME/environment
        # direct all user startup reads to these fixtures.
        def login(code):
            result = subprocess.run([shutil.which("zsh"), "-d", "-ilc", code], env=self.env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result
        result = login('[[ ${(t)ZDOTDIR} != *export* ]] || exit 11\n'
                       'command jq -cn --arg directory "$ZDOTDIR" '
                       "'{missing:true,startupDirectory:$directory}'")
        self.assertNotIn("ZDOTDIR", self.env)
        self.assertEqual(json.loads(result.stdout)["startupDirectory"], str(dot))
        self.install(shell="zsh")
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".zlogin").exists())
        login("claude --fixture\n")
        self.assertIn("BROWSER=false", self.log.read_text())
        self.assertIn("TZ=Australia/Sydney", self.log.read_text())
        self.assertIn("ARG=--fixture", self.log.read_text())
        self.assertNotIn("--bad-login", self.log.read_text())

    def test_invalid_zsh_startup_directory_does_not_mutate(self):
        for directory in ("relative", str(self.home / "absent"), str(self.home) + "\n"):
            (self.home / ".zshenv").write_text("ZDOTDIR=" + shlex.quote(directory) + "\n")
            result = self.install(shell="zsh", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.private.exists())

    def test_unsafe_zlogin_leaves_zshrc_and_private_state_untouched(self):
        rc = self.home / ".zshrc"
        rc.write_bytes(b"# unchanged zshrc\n")
        login = self.home / ".zlogin"
        login.symlink_to(rc)
        self.assertNotEqual(self.install(shell="zsh", check=False).returncode, 0)
        self.assertEqual(rc.read_bytes(), b"# unchanged zshrc\n")
        self.assertFalse(self.private.exists())

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

    def test_corrupt_private_record_rejected(self):
        self.install()
        (self.private / "managed.json").write_text("{}")
        before = (self.home / ".bashrc").read_bytes()
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual((self.home / ".bashrc").read_bytes(), before)

    def pty_launch(self, shell, args):
        stdout_path = self.home / "pty.stdout"
        code = '. "$HOME/.ccacc-shell/hook.sh"\n'
        if args is not None:
            code += 'claude ' + shlex.join(args)
        flags = ["--noprofile", "--norc"] if shell == "bash" else ["-d", "-f"]
        pid, master = pty.fork()
        if pid == 0:
            output = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(output, 1)
            os.close(output)
            os.execve(shutil.which(shell), [shell, *flags, "-i", "-c", code], self.env)
        output = b""
        status = None
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b""
                    output += chunk
                self.assertNotIn(b"YOLO?", output)
                done, child_status = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = os.waitstatus_to_exitcode(child_status)
                    break
            self.assertIsNotNone(status, output.decode(errors="replace"))
        finally:
            os.close(master)
        return status, output.decode(errors="replace"), stdout_path.read_text()

    def test_pty_source_never_prompts_or_launches(self):
        self.install()
        for shell in ("bash", "zsh"):
            status, stderr, stdout = self.pty_launch(shell, None)
            self.assertEqual(status, 0)
            self.assertNotIn("YOLO", stderr + stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.home / "timezone.calls").exists())

    def test_pty_invalid_timezone_fails_before_yolo_prompt(self):
        self.install()
        self.zone.write_text("Invalid/Zone\n")
        for shell in ("bash", "zsh"):
            status, stderr, _ = self.pty_launch(shell, [])
            self.assertEqual(status, 1)
            self.assertNotIn("YOLO", stderr)
        self.assertFalse(self.log.exists())

    def test_pty_launch_forwards_every_argument_without_prompting(self):
        self.install()
        for shell in ("bash", "zsh"):
            for args in ([], ["--help"], ["--version"], ["/login"], ["--print", "two words"],
                         ["--dangerously-skip-permissions", "", "literal;argument"]):
                status, stderr, _ = self.pty_launch(shell, args)
                self.assertEqual(status, 0)
                self.assertNotIn("YOLO", stderr)
        self.assertEqual(self.log.read_text().count("ARG=--dangerously-skip-permissions"), 2)
        hook = (self.private / "hook.sh").read_text()
        self.assertNotIn("read -r", hook)
        self.assertNotIn("__ccacc_prompt", hook)

    def test_non_tty_preserves_stdin_and_caller_arguments_without_prompt(self):
        self.install()
        self.claude.write_text('#!/bin/bash\nIFS= read -r data\nprintf "%s\\n" "$data"\n'
                               'printf "[%s]\\n" "$@"\nexit 29\n')
        for shell in ("bash", "zsh"):
            flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
            result = subprocess.run([shutil.which(shell), *flags, "-c",
                '. "$HOME/.ccacc-shell/hook.sh"; claude -p "two words" ""'],
                env=self.env, input="payload survives\n", text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 29)
            self.assertEqual(result.stdout, "payload survives\n[-p]\n[two words]\n[]\n")
            self.assertEqual(result.stderr, "")

    def test_publish_failure_rolls_back_hook_record_and_startup_files(self):
        self.install()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file()}
        result = self.shell(f"source {shlex.quote(str(LIBRARY))}\n" + r'''
_ccacc_shell_replace() {
    if [ "$2" = "$HOME/.bash_profile" ]; then return 91; fi
    mv -f "$1" "$2"
}
''' + f"ccacc_install_shell_hook {shlex.quote(str(self.ccacc))}\n", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_unknown_shell_and_missing_zdotdir(self):
        self.assertNotEqual(self.install(shell="fish", check=False).returncode, 0)
        self.assertFalse(self.private.exists())
        self.env["ZDOTDIR"] = str(self.home / "absent")
        self.assertNotEqual(self.install(shell="zsh", check=False).returncode, 0)
        self.assertFalse(self.private.exists())


if __name__ == "__main__":
    unittest.main()
