"""Offline installer contract tests: fake downloads/core and isolated HOME/PATH."""

import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
TOOLS = ("bash", "mkdir", "mktemp", "chmod", "cp", "mv", "rm")
CORE = r'''#!/bin/bash
set -eu
printf '%s\0' "$0" "$@" >> "$SETUP_LOG"
[ "$#" = 1 ] && [ "$1" = configure-shell ] || exit 91
[ "$0" = "$EXPECTED_CORE" ] || exit 92
[ -x "$0" ] && [ -r "${0%/*}/ccacc_shell.sh" ] || exit 93
. "${0%/*}/ccacc_shell.sh"
[ "$FIXTURE_HELPER" = ready ] || exit 94
if IFS= read -r input; then
    printf 'setup consumed stdin\n' >&2
    exit 95
fi
exit "${SETUP_EXIT:-0}"
'''
HELPER = "#!/bin/bash\nFIXTURE_HELPER=ready\n"
CURL = r'''#!/bin/bash
set -eu
printf '%s\0' "$@" >> "$DOWNLOAD_LOG"
output= url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output=$2; shift 2 ;;
        --proto|--proto-redir) [ "$2" = '=https' ] || exit 87; shift 2 ;;
        https://raw.githubusercontent.com/*) url=$1; shift ;;
        --disable|--fail|--silent|--show-error|--location) shift ;;
        *) exit 88 ;;
    esac
done
file=${url##*/}
case "$file" in ccacc|ccacc_shell.sh) ;; *) exit 89 ;; esac
printf '%s\n' "$output" >> "$STAGE_LOG"
if [ "${DOWNLOAD_FAIL:-}" = "$file" ]; then
    printf 'partial content' > "$output"
    exit 22
fi
/bin/cp "$PAYLOADS/$file" "$output"
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".orca").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".orca", prefix="installer-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / "home with spaces"
        self.home.mkdir()
        self.bin = self.fixture / "tools"
        self.bin.mkdir()
        # Only explicitly allowed utilities exist on PATH. No Claude, Keychain,
        # account tools, package managers, or network clients can be launched.
        for name in TOOLS:
            target = shutil.which(name)
            self.assertIsNotNone(target, name)
            (self.bin / name).symlink_to(target)
        self.executable("jq", "#!/bin/bash\nexit 0\n")
        self.executable("curl", CURL)
        self.payloads = self.fixture / "payloads"
        self.payloads.mkdir()
        (self.payloads / "ccacc").write_text(CORE)
        (self.payloads / "ccacc_shell.sh").write_text(HELPER)
        self.prefix = self.home / ".local/bin"
        self.setup_log = self.fixture / "setup.log"
        self.download_log = self.fixture / "downloads.log"
        self.stage_log = self.fixture / "stages.log"
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin),
                        SHELL="/bin/bash", TMPDIR=str(self.fixture),
                        PAYLOADS=str(self.payloads), SETUP_LOG=str(self.setup_log),
                        DOWNLOAD_LOG=str(self.download_log), STAGE_LOG=str(self.stage_log),
                        EXPECTED_CORE=str(self.prefix / "ccacc"))
        for name in ("BASH_ENV", "ENV", "ZDOTDIR", "CLAUDE_CONFIG_DIR",
                     "CCACC_INSTALL_REPO", "CCACC_INSTALL_REF", "SETUP_EXIT", "DOWNLOAD_FAIL"):
            self.env.pop(name, None)

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o700)

    def run_installer(self, *args, success=True, stdin="", command=None, retained_stage=False):
        result = subprocess.run(command or ["/bin/bash", str(INSTALLER), *args],
                                input=stdin, text=True, capture_output=True,
                                env=self.env, timeout=15)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        self.assertEqual(bool(list(self.prefix.glob(".ccacc-install.*"))), retained_stage)
        # The fake core never writes any profiles, accounts or authentication.
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".claude.json").exists())
        self.assertFalse((self.home / ".bashrc").exists())
        return result

    def log_args(self, path):
        return path.read_bytes().decode().rstrip("\0").split("\0") if path.exists() else []

    def existing_pair(self):
        self.prefix.mkdir(parents=True, exist_ok=True)
        for name in ("ccacc", "ccacc_shell.sh"):
            path = self.prefix / name
            path.write_text("old " + name)
            path.chmod(0o640)
        (self.prefix / "unrelated").write_text("keep me")

    def assert_old_pair(self):
        for name in ("ccacc", "ccacc_shell.sh"):
            path = self.prefix / name
            self.assertEqual(path.read_text(), "old " + name)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual((self.prefix / "unrelated").read_text(), "keep me")
        self.assertFalse(self.setup_log.exists())

    def test_default_installs_both_files_and_sets_modes(self):
        result = self.run_installer()
        self.assertEqual((self.prefix / "ccacc").read_text(), CORE)
        self.assertEqual((self.prefix / "ccacc_shell.sh").read_text(), HELPER)
        self.assertEqual(stat.S_IMODE((self.prefix / "ccacc").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.prefix / "ccacc_shell.sh").stat().st_mode), 0o600)
        args = self.log_args(self.download_log)
        for file in ("ccacc", "ccacc_shell.sh"):
            self.assertIn("https://raw.githubusercontent.com/geminiwen/ccswap/main/" + file, args)
        for flag in ("--disable", "--fail", "--location", "--proto", "--proto-redir"):
            self.assertEqual(args.count(flag), 2)
        self.assertEqual(self.log_args(self.setup_log), [str(self.prefix / "ccacc"), "configure-shell"])
        self.assertIn("source " + shlex.quote(str(self.home / ".ccacc-shell/hook.sh")),
                      self.shell_unescape_source(result.stdout))
        self.assertIn("new terminal", result.stdout)
        for stage in self.stage_log.read_text().splitlines():
            self.assertFalse(Path(stage).parent.exists())

    @staticmethod
    def shell_unescape_source(output):
        # Bash %q uses backslash quoting, whereas shlex.quote uses single quotes.
        for line in output.splitlines():
            if line.strip().startswith("source "):
                words = shlex.split(line)
                return "source " + shlex.quote(words[1])
        return ""

    def test_custom_prefix_with_spaces_and_source_override(self):
        self.prefix = self.fixture / "custom tools/bin directory"
        self.env.update(EXPECTED_CORE=str(self.prefix / "ccacc"),
                        CCACC_INSTALL_REPO="fixture-owner/fixture.repo", CCACC_INSTALL_REF="release/test-1")
        self.run_installer("--prefix", str(self.prefix) + "/")
        self.assertTrue((self.prefix / "ccacc").is_file())
        args = self.log_args(self.download_log)
        for name in ("ccacc", "ccacc_shell.sh"):
            self.assertIn("https://raw.githubusercontent.com/fixture-owner/fixture.repo/release/test-1/" + name, args)
        self.assertFalse((self.home / ".local").exists())

    def test_stdin_is_not_consumed_by_setup(self):
        command = ["/bin/bash", "-c", 'bash "$1"; status=$?; IFS= read -r line; '
                   'printf "remaining=%s\\n" "$line"; exit "$status"', "fixture", str(INSTALLER)]
        result = self.run_installer(stdin="sentinel input\n", command=command)
        self.assertIn("remaining=sentinel input", result.stdout)

    def test_curl_pipe_bash_shape(self):
        # Feed the actual script through stdin, just as curl | bash does.
        self.run_installer(stdin=INSTALLER.read_text(), command=["/bin/bash"])
        self.assertEqual(self.log_args(self.setup_log), [str(self.prefix / "ccacc"), "configure-shell"])

    def test_missing_prerequisites_fail_before_download_or_clobber(self):
        for name in ("curl", "jq", *TOOLS):
            with self.subTest(tool=name):
                self.existing_pair()
                path = self.bin / name
                hidden = self.bin / (name + ".hidden")
                path.rename(hidden)
                try:
                    result = self.run_installer(success=False)
                    self.assertIn("missing prerequisite: " + name, result.stderr)
                    self.assertFalse(self.download_log.exists())
                    self.assert_old_pair()
                finally:
                    hidden.rename(path)

    def test_partial_download_of_either_file_never_clobbers(self):
        self.existing_pair()
        for name in ("ccacc", "ccacc_shell.sh"):
            with self.subTest(file=name):
                self.env["DOWNLOAD_FAIL"] = name
                result = self.run_installer(success=False)
                self.assertIn("download failed for " + name, result.stderr)
                self.assert_old_pair()

    def test_empty_and_bad_bash_downloads_never_clobber(self):
        self.existing_pair()
        for name, original in (("ccacc", CORE), ("ccacc_shell.sh", HELPER)):
            for content in ("", "#!/bin/bash\nif then\n"):
                with self.subTest(file=name, content=content):
                    (self.payloads / name).write_text(content)
                    self.run_installer(success=False)
                    self.assert_old_pair()
            (self.payloads / name).write_text(original)

    def test_setup_error_reports_installed_state_and_exact_repair_command(self):
        self.env["SETUP_EXIT"] = "42"
        result = self.run_installer(success=False)
        self.assertIn("files are installed, but shell setup failed", result.stderr)
        self.assertIn("rerun this installer", result.stderr)
        line = next(line for line in result.stderr.splitlines() if line.startswith("  bash "))
        self.assertEqual(shlex.split(line), ["bash", str(self.prefix / "ccacc"), "configure-shell"])
        self.assertEqual((self.prefix / "ccacc").read_text(), CORE)
        self.assertEqual((self.prefix / "ccacc_shell.sh").read_text(), HELPER)
        self.assertNotIn("Setup complete", result.stdout)

    def test_repeat_installs_preserve_unrelated_files(self):
        self.existing_pair()
        for _ in range(2):
            self.run_installer()
            self.assertEqual((self.prefix / "unrelated").read_text(), "keep me")
        self.assertEqual(self.log_args(self.setup_log),
                         [str(self.prefix / "ccacc"), "configure-shell"] * 2)

    def test_replacement_does_not_modify_hard_linked_old_file(self):
        self.existing_pair()
        other = self.fixture / "other core name"
        os.link(self.prefix / "ccacc", other)
        self.run_installer()
        self.assertEqual(other.read_text(), "old ccacc")

    def test_staging_is_private_during_download(self):
        # Observe permissions while the directory still exists, before cleanup.
        self.executable("curl", "#!" + sys.executable + "\n" + r'''
import os
from pathlib import Path
import shutil
import stat
import sys
args = sys.argv[1:]
output = Path(args[args.index('--output') + 1])
assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
shutil.copyfile(Path(os.environ['PAYLOADS']) / output.name, output)
''')
        self.run_installer()

    def test_second_rename_failure_restores_existing_pair(self):
        self.existing_pair()
        real_mv = shutil.which("mv")
        (self.bin / "mv").unlink()
        self.executable("mv", '#!/bin/bash\n'
                        'if [ "${3##*/}" = ccacc ]; then exit 1; fi\n'
                        'exec ' + shlex.quote(real_mv) + ' "$@"\n')
        result = self.run_installer(success=False)
        self.assertIn("deployment failed", result.stderr)
        self.assert_old_pair()

    def test_second_rename_failure_removes_new_partial_pair(self):
        real_mv = shutil.which("mv")
        (self.bin / "mv").unlink()
        self.executable("mv", '#!/bin/bash\n'
                        'if [ "${3##*/}" = ccacc ]; then exit 1; fi\n'
                        'exec ' + shlex.quote(real_mv) + ' "$@"\n')
        self.run_installer(success=False)
        self.assertFalse((self.prefix / "ccacc").exists())
        self.assertFalse((self.prefix / "ccacc_shell.sh").exists())
        self.assertFalse(self.setup_log.exists())

    def test_failed_rollback_keeps_backups_and_reports_partial_state(self):
        self.existing_pair()
        (self.bin / "mv").unlink()
        self.executable("mv", "#!/bin/bash\nexit 1\n")
        result = self.run_installer(success=False, retained_stage=True)
        self.assertIn("rollback failed", result.stderr)
        self.assertIn("installed pair may be incomplete", result.stderr)
        stages = list(self.prefix.glob(".ccacc-install.*"))
        self.assertEqual(len(stages), 1)
        self.assertIn(str(stages[0]), result.stderr)
        for name in ("ccacc", "ccacc_shell.sh"):
            self.assertEqual((stages[0] / ("old-" + name)).read_text(), "old " + name)
        self.assert_old_pair()

    def test_symlink_or_nonregular_targets_are_rejected(self):
        self.prefix.mkdir(parents=True)
        victim = self.fixture / "victim"
        victim.write_text("unchanged")
        for name in ("ccacc", "ccacc_shell.sh"):
            with self.subTest(file=name):
                path = self.prefix / name
                for target in (victim, self.fixture / "does-not-exist"):
                    path.symlink_to(target)
                    self.run_installer(success=False)
                    path.unlink()
                path.mkdir()
                self.run_installer(success=False)
                path.rmdir()
        self.assertEqual(victim.read_text(), "unchanged")
        self.assertFalse(self.download_log.exists())

    def test_symlink_prefix_ancestor_rejected_without_writing_through_it(self):
        target = self.fixture / "real directory"
        target.mkdir()
        link = self.fixture / "link"
        link.symlink_to(target, target_is_directory=True)
        self.run_installer("--prefix", str(link / "bin"), success=False)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(self.download_log.exists())

    def test_invalid_paths_arguments_and_source_overrides_fail_before_download(self):
        cases = [("--prefix", "relative/bin"), ("--prefix", str(self.fixture / "../bin")),
                 ("--prefix", str(self.fixture) + "//bin"), ("--prefix", "/"),
                 ("--prefix",), ("--prefix", ""), ("--unknown",),
                 ("--prefix", str(self.prefix), "--prefix", str(self.prefix))]
        for args in cases:
            with self.subTest(args=args):
                self.run_installer(*args, success=False)
        for name, values in (("CCACC_INSTALL_REPO", ["a", "a/b/c", "a/..", "a:b/c", "a/b?token=secret"]),
                             ("CCACC_INSTALL_REF", ["../main", "/main", "main/", "main?token=secret"])):
            for value in values:
                with self.subTest(name=name, value=value):
                    self.env[name] = value
                    result = self.run_installer(success=False)
                    self.assertNotIn("token=secret", result.stdout + result.stderr)
            self.env.pop(name)
        self.assertFalse(self.download_log.exists())
        self.assertFalse(self.prefix.exists())

    def test_help_is_read_only_and_needs_no_prerequisites(self):
        self.env["PATH"] = str(self.fixture / "empty")
        result = self.run_installer("--help")
        self.assertIn("--prefix", result.stdout)
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
