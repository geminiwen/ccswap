"""Offline installer contract tests: fake downloads/core and isolated HOME/PATH."""

import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import termios
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
TOOLS = ("bash", "jq", "mkdir", "mktemp", "chmod", "cp", "mv", "rm", "ln")
COMMIT = "1234567890abcdef" * 2 + "12345678"
NEXT_COMMIT = "abcdef1234567890" * 2 + "abcdef12"
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
        https://raw.githubusercontent.com/*|https://api.github.com/*) url=$1; shift ;;
        --disable|--fail|--silent|--show-error|--location) shift ;;
        *) exit 88 ;;
    esac
done
case "$url" in
    https://api.github.com/*)
        [ "${API_FAIL:-}" != yes ] || exit 22
        printf '%s' "$COMMIT_RESPONSE" > "$output"
        exit 0 ;;
esac
file=${url##*/}
case "$file" in ccacc|ccacc_shell.sh) ;; *) exit 89 ;; esac
printf '%s\n' "$output" >> "$STAGE_LOG"
if [ "${DOWNLOAD_FAIL:-}" = "$file" ]; then
    printf 'partial content' > "$output"
    exit 22
fi
/bin/cp "$PAYLOADS/$file" "$output"
if [ "${MUTABLE_REF_ADVANCES:-}" = yes ]; then
    revision=${url%/*}; revision=${revision##*/}
    if [ "$revision" = main ] && [ -f "$BRANCH_STATE" ]; then
        printf '#!/bin/bash\nFIXTURE_HELPER=changed\n' > "$output"
    fi
    if [ "$file" = ccacc ]; then printf '%s' "$NEXT_COMMIT" > "$BRANCH_STATE"; fi
fi
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
        self.executable("curl", CURL)
        self.executable("uname", '#!/bin/bash\n[ "$1" = -s ] || exit 86\nprintf "%s\\n" "${FAKE_UNAME-Darwin}"\n')
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
                        COMMIT_RESPONSE='{"sha":"' + COMMIT + '"}',
                        BRANCH_STATE=str(self.fixture / "branch-state"), NEXT_COMMIT=NEXT_COMMIT,
                        EXPECTED_CORE=str(self.prefix / "ccacc"))
        for name in ("BASH_ENV", "ENV", "ZDOTDIR", "CLAUDE_CONFIG_DIR",
                     "CCACC_INSTALL_REPO", "CCACC_INSTALL_REF", "SETUP_EXIT", "DOWNLOAD_FAIL",
                     "API_FAIL", "MUTABLE_REF_ADVANCES", "FAKE_UNAME"):
            self.env.pop(name, None)

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o700)

    def run_installer(self, *args, success=True, stdin="", command=None, retained_stage=False):
        result = subprocess.run(command or ["/bin/bash", str(INSTALLER), *args],
                                input=stdin, text=True, capture_output=True,
                                env=self.env, timeout=15, start_new_session=True)
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
            self.assertIn("https://raw.githubusercontent.com/geminiwen/ccswap/" + COMMIT + "/" + file, args)
        self.assertEqual(args.count("https://api.github.com/repos/geminiwen/ccswap/commits/main"), 1)
        for flag in ("--disable", "--fail", "--location", "--proto", "--proto-redir"):
            self.assertEqual(args.count(flag), 3)
        self.assertEqual(self.log_args(self.setup_log), [str(self.prefix / "ccacc"), "configure-shell"])
        self.assertIn("source " + shlex.quote(str(self.home / ".ccacc-shell/hook.sh")),
                      self.shell_unescape_source(result.stdout))
        self.assertIn("new terminal", result.stdout)
        line = next(line.strip() for line in result.stdout.splitlines() if line.strip().startswith("export PATH="))
        probe = subprocess.run(["/bin/bash", "-c", line + "\ncommand -v ccacc"], env=self.env,
                               text=True, capture_output=True, timeout=15, check=True)
        self.assertEqual(probe.stdout.strip(), str(self.prefix / "ccacc"))
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
            self.assertIn("https://raw.githubusercontent.com/fixture-owner/fixture.repo/" + COMMIT + "/" + name, args)
        self.assertIn("https://api.github.com/repos/fixture-owner/fixture.repo/commits/release%2Ftest-1", args)
        self.assertFalse((self.home / ".local").exists())

    def test_main_advances_but_downloads_use_one_resolved_commit(self):
        self.env["MUTABLE_REF_ADVANCES"] = "yes"
        self.run_installer()
        self.assertEqual(Path(self.env["BRANCH_STATE"]).read_text(), NEXT_COMMIT)
        urls = [arg for arg in self.log_args(self.download_log) if arg.startswith("https://")]
        self.assertEqual(urls, ["https://api.github.com/repos/geminiwen/ccswap/commits/main",
                               "https://raw.githubusercontent.com/geminiwen/ccswap/" + COMMIT + "/ccacc",
                               "https://raw.githubusercontent.com/geminiwen/ccswap/" + COMMIT + "/ccacc_shell.sh"])

    def test_explicit_full_sha_bypasses_api(self):
        for commit in (COMMIT, COMMIT.upper()):
            with self.subTest(commit=commit):
                self.download_log.unlink(missing_ok=True)
                self.env.update(CCACC_INSTALL_REF=commit, API_FAIL="yes")
                self.run_installer()
                urls = [arg for arg in self.log_args(self.download_log) if arg.startswith("https://")]
                self.assertEqual(urls, ["https://raw.githubusercontent.com/geminiwen/ccswap/" + commit + "/" + name
                                        for name in ("ccacc", "ccacc_shell.sh")])

    def test_api_failure_and_invalid_responses_never_clobber_or_fetch_files(self):
        self.existing_pair()
        responses = ["", "not json", "null", "[]", "{}", '{"sha":null}',
                     '{"sha":1234567890123456789012345678901234567890}',
                     '{"sha":["' + COMMIT + '"]}', '{"sha":"' + COMMIT + 'g"}',
                     '{"sha":"' + COMMIT[:-1] + '"}', '{"sha":"' + "g" * 40 + '"}',
                     '{"sha":"' + COMMIT + '\\n"}',
                     '{"sha":"' + COMMIT + '"}\n{"sha":"' + NEXT_COMMIT + '"}']
        for api_fail, response in [("yes", self.env["COMMIT_RESPONSE"]),
                                   *(("", response) for response in responses)]:
            with self.subTest(api_fail=api_fail, response=response):
                self.download_log.unlink(missing_ok=True)
                self.env.update(API_FAIL=api_fail, COMMIT_RESPONSE=response)
                self.run_installer(success=False)
                self.assert_old_pair()
                self.assertFalse(self.stage_log.exists())
                urls = [arg for arg in self.log_args(self.download_log) if arg.startswith("https://")]
                self.assertEqual(urls, ["https://api.github.com/repos/geminiwen/ccswap/commits/main"])

    def test_short_sha_is_resolved_through_api(self):
        self.env["CCACC_INSTALL_REF"] = COMMIT[:12]
        self.run_installer()
        self.assertIn("https://api.github.com/repos/geminiwen/ccswap/commits/" + COMMIT[:12],
                      self.log_args(self.download_log))

    def test_missing_prefix_on_path_prints_executable_export(self):
        self.prefix = self.fixture / "bin with 'quotes' $dollar `ticks`"
        self.env["EXPECTED_CORE"] = str(self.prefix / "ccacc")
        result = self.run_installer("--prefix", str(self.prefix))
        line = next(line.strip() for line in result.stdout.splitlines() if line.strip().startswith("export PATH="))
        probe = subprocess.run(["/bin/bash", "-c", line + '\ncommand -v ccacc\nprintf "%s" "$PATH"'],
                               env=self.env, text=True, capture_output=True, timeout=15, check=True)
        self.assertEqual(probe.stdout, str(self.prefix / "ccacc") + "\n" + str(self.prefix) + ":" + self.env["PATH"])

    def test_prefix_already_on_path_needs_no_export(self):
        self.env["PATH"] += ":" + str(self.prefix)
        self.assertNotIn("export PATH=", self.run_installer().stdout)

    def test_colon_prefix_prints_usable_absolute_command(self):
        self.prefix = self.fixture / "bin:with colon"
        self.env["EXPECTED_CORE"] = str(self.prefix / "ccacc")
        result = self.run_installer("--prefix", str(self.prefix))
        line = next(line for line in result.stdout.splitlines() if line.startswith("  bash "))
        self.assertEqual(shlex.split(line), ["bash", str(self.prefix / "ccacc")])
        self.assertNotIn("export PATH=", result.stdout)

    def test_stdin_is_not_consumed_by_setup(self):
        command = ["/bin/bash", "-c", 'bash "$1"; status=$?; IFS= read -r line; '
                   'printf "remaining=%s\\n" "$line"; exit "$status"', "fixture", str(INSTALLER)]
        result = self.run_installer(stdin="sentinel input\n", command=command)
        self.assertIn("remaining=sentinel input", result.stdout)

    def test_curl_pipe_bash_shape(self):
        # Feed the actual script through stdin, just as curl | bash does.
        self.run_installer(stdin=INSTALLER.read_text(), command=["/bin/bash"])
        self.assertEqual(self.log_args(self.setup_log), [str(self.prefix / "ccacc"), "configure-shell"])

    def run_with_tty(self, answer):
        # Give the installer a controlling terminal, as curl | bash in Terminal has.
        # macOS waits for unread tty output on exit, so drain the master meanwhile.
        master, slave = os.openpty()
        def attach():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        chunks = []
        def drain():
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                chunks.append(chunk)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        os.write(master, answer.encode())
        try:
            result = subprocess.run(["/bin/bash"], input=INSTALLER.read_text(), text=True,
                                    capture_output=True, env=self.env, timeout=15,
                                    preexec_fn=attach, pass_fds=(slave,))
        finally:
            os.close(slave)
            reader.join(2)
            os.close(master)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        return result, b"".join(chunks).decode(errors="replace")

    @property
    def settings(self):
        return self.home / ".ccacc/settings.json"

    def test_no_terminal_writes_no_settings(self):
        result = self.run_installer(stdin=INSTALLER.read_text(), command=["/bin/bash"])
        self.assertFalse((self.home / ".ccacc").exists())
        self.assertIn('{"alwaysYOLO": true}', result.stdout)
        self.assertIn("Setup complete", result.stdout)

    def test_terminal_yes_saves_always_yolo_true(self):
        result, prompt = self.run_with_tty("y\n")
        self.assertIn("switching accounts? (y/N)", prompt)
        self.assertEqual(json.loads(self.settings.read_text()), {"alwaysYOLO": True})
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.settings.parent.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in self.settings.parent.iterdir()], ["settings.json"])
        self.assertIn("Saved alwaysYOLO=true", result.stdout)

    def test_terminal_blank_answer_defaults_to_false(self):
        result, _ = self.run_with_tty("\n")
        self.assertEqual(json.loads(self.settings.read_text()), {"alwaysYOLO": False})
        self.assertIn("Saved alwaysYOLO=false", result.stdout)

    def test_existing_settings_are_kept_without_asking(self):
        self.settings.parent.mkdir(mode=0o700)
        self.settings.write_text('{"alwaysYOLO": true, "other": 1}')
        result, prompt = self.run_with_tty("n\n")
        self.assertNotIn("(y/N)", prompt)
        self.assertEqual(self.settings.read_text(), '{"alwaysYOLO": true, "other": 1}')
        self.assertIn("Keeping existing settings", result.stdout)

    def test_unsavable_settings_warn_but_install_succeeds(self):
        (self.home / ".ccacc").write_text("not a directory")
        result, _ = self.run_with_tty("y\n")
        self.assertIn("settings were not saved", result.stderr)
        self.assertIn("Setup complete", result.stdout)

    def test_missing_prerequisites_fail_before_download_or_clobber(self):
        for name in ("curl", *TOOLS):
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

    def test_non_macos_is_refused_before_any_download_or_write(self):
        self.existing_pair()
        for platform in ("Linux", ""):
            with self.subTest(platform=platform):
                self.env["FAKE_UNAME"] = platform
                result = self.run_installer(success=False)
                self.assertIn("supports macOS only", result.stderr)
                self.assertFalse(self.download_log.exists())
                self.assertFalse(self.stage_log.exists())
                self.assert_old_pair()

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
if args[-1].startswith('https://api.github.com/'):
    output.write_text(os.environ['COMMIT_RESPONSE'])
else:
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
        for name, values in (("CCACC_INSTALL_REPO", ["a", "a/b/c", "a/..", "a:b/c", "a/b?token=secret",
                                                    "/b", "a/", "./b", "a//b", "a/b#fragment"]),
                             ("CCACC_INSTALL_REF", ["../main", "/main", "main/", "main?token=secret",
                                                   "./main", "main/.", "main/./next", "main//next",
                                                   "main%2Fnext", "main#fragment", COMMIT + "\n"])):
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
