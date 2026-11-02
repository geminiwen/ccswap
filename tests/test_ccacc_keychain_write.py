"""Keychain writes go through `security` like Claude Code's: fake security, no real keychain."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# save/use read the weekly reset from Keychain and network; fixtures forbid both.
os.environ["CCACC_NO_WEEKLY_RESET"] = "1"


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "ccacc").read_text().rsplit('main "$@"', 1)[0]
SERVICE = "Claude Code-credentials"
PREFIX_LENGTH = len('add-generic-password -U -a "') + len('" -s "') + len('" -X ')
# Mirrors the store/read behaviour of /usr/bin/security for generic passwords.
FAKE_SECURITY = r'''
import json, os, shlex, sys

log, store_path = os.environ["SEC_LOG"], os.environ["SEC_STORE"]
store = json.load(open(store_path)) if os.path.exists(store_path) else {}

def record(entry):
    with open(log, "a") as handle:
        handle.write(json.dumps(entry) + "\n")

def add(args):
    assert args[:2] == ["add-generic-password", "-U"], args
    options = dict(zip(args[2::2], args[3::2]))
    assert set(options) == {"-a", "-s", "-X"}, args
    bytes.fromhex(options["-X"])
    store[options["-s"] + "|" + options["-a"]] = options["-X"]

argv = sys.argv[1:]
if argv == ["-i"]:
    data = sys.stdin.read()
    record({"argv": argv, "stdin": data})
    limit = int(os.environ.get("FAKE_LINE_LIMIT", "0"))
    for line in data.splitlines():
        args = shlex.split(line[:limit] if limit else line)
        # Like the real line buffer: silently keep only a whole-byte prefix of the data.
        args[-1] = args[-1][:len(args[-1]) // 2 * 2]
        add(args)
elif argv and argv[0] == "add-generic-password":
    record({"argv": argv})
    add(argv)
elif argv and argv[0] == "find-generic-password":
    record({"argv": argv})
    options = dict(zip(argv[1::2], argv[2::2]))
    value = store.get(options.get("-s", "") + "|" + options.get("-a", ""))
    if value is None:
        sys.exit(44)
    raw = bytes.fromhex(value)
    # security prints secrets containing non-ASCII bytes as hex.
    print(raw.decode() if raw.isascii() else value)
    sys.exit(0)
else:
    record({"argv": argv, "unexpected": True})
    sys.exit(99)
json.dump(store, open(store_path, "w"))
'''


class KeychainWriteTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".orca").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".orca", prefix="keychain-write-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / "home"
        self.home.mkdir()
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        self.log = self.fixture / "security.log"
        self.store = self.fixture / "store.json"
        (self.fixture / "fake_security.py").write_text(FAKE_SECURITY)
        self.executable("security", f'#!/bin/bash\nexec {sys.executable} -I '
                        f'"{self.fixture}/fake_security.py" "$@"\n')
        self.env = dict(os.environ, HOME=str(self.home), USER="fixture-user", TMPDIR=str(self.fixture),
                        PATH=f"{self.bin}:{os.environ['PATH']}", SEC_LOG=str(self.log),
                        SEC_STORE=str(self.store))
        for name in ("BASH_ENV", "ENV", "FAKE_LINE_LIMIT"):
            self.env.pop(name, None)

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def run_body(self, body, secret, success=True):
        env = dict(self.env, SECRET=secret)
        result = subprocess.run(["/bin/bash", "-c", SOURCE + "\n" + body], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def stored(self, service=SERVICE, account="fixture-user"):
        return bytes.fromhex(json.loads(self.store.read_text())[service + "|" + account]).decode()

    def secret_of_line_length(self, length, account="fixture-user", service=SERVICE):
        payload = (length - PREFIX_LENGTH - len(account) - len(service)) // 2
        wrapper = '{"claudeAiOauth":{"accessToken":"fixture-marker-"}}'
        return wrapper.replace("fixture-marker-", "fixture-marker-" + "x" * (payload - len(wrapper)))

    def test_normal_write_uses_security_stdin_with_hex_and_never_argv(self):
        secret = json.dumps({"claudeAiOauth": {"accessToken": "fixture-marker-é"},
                             "mcpOAuth": {"shared": {"accessToken": "fixture-shared"}}})
        self.run_body('write_credentials "$SECRET"', secret)
        self.assertEqual(self.stored(), secret)
        writes = [call for call in self.calls() if "find-generic-password" not in call["argv"]]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["argv"], ["-i"])
        hex_secret = secret.encode().hex()
        self.assertEqual(writes[0]["stdin"], 'add-generic-password -U -a "fixture-user" '
                         f'-s "{SERVICE}" -X {hex_secret}\n')
        for call in self.calls():
            argv = " ".join(call["argv"])
            self.assertNotIn("fixture-marker", argv)
            self.assertNotIn(hex_secret, argv)
        # The post-write verification read still happens.
        self.assertIn(["find-generic-password", "-s", SERVICE, "-a", "fixture-user", "-w"],
                      [call["argv"] for call in self.calls()])

    def test_line_limit_boundary_switches_to_argv_fallback_with_warning(self):
        for length, interactive in ((4000, True), (4002, False)):
            with self.subTest(length=length):
                self.log.unlink(missing_ok=True)
                secret = self.secret_of_line_length(length)
                hex_secret = secret.encode().hex()
                self.assertEqual(PREFIX_LENGTH + len("fixture-user") + len(SERVICE) + len(hex_secret), length)
                result = self.run_body('write_credentials "$SECRET"', secret)
                self.assertEqual(self.stored(), secret)
                writes = [call["argv"] for call in self.calls() if call["argv"][0] != "find-generic-password"]
                if interactive:
                    self.assertEqual(writes, [["-i"]])
                    self.assertNotIn("Warning", result.stderr)
                else:
                    self.assertEqual(writes, [["add-generic-password", "-U", "-a", "fixture-user",
                                               "-s", SERVICE, "-X", hex_secret]])
                    self.assertIn("too large for 'security -i'", result.stderr)

    def test_truncated_or_failed_write_is_reported_as_failure(self):
        secret = self.secret_of_line_length(3000)
        self.env["FAKE_LINE_LIMIT"] = "2000"
        self.run_body('write_credentials "$SECRET"', secret, success=False)
        self.env.pop("FAKE_LINE_LIMIT")
        self.executable("security", "#!/bin/bash\nexit 2\n")
        self.run_body('write_credentials "$SECRET"', '{"claudeAiOauth":{}}', success=False)

    def test_unsafe_names_and_empty_secret_are_refused_without_calling_security(self):
        for body in ('keychain_write "bad\\"service" fixture-user "$SECRET"',
                     'keychain_write ccacc-test fixture\\\\user "$SECRET"',
                     'keychain_write "$KEYCHAIN_SERVICE" "$USER" ""'):
            with self.subTest(body=body):
                self.run_body(body, '{"claudeAiOauth":{}}', success=False)
                self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
