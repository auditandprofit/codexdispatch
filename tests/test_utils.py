import os
import subprocess
import tempfile
import unittest

from codexdispatch.utils import _invoke_codex


class TestInvokeCodex(unittest.TestCase):
    def test_stderr_captured_on_failure(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        script = os.path.join(tmpdir.name, "fail.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stderr.write('oops\\n')\n"
                "sys.exit(1)\n"
            )
        os.chmod(script, 0o755)

        with self.assertRaises(subprocess.CalledProcessError) as cm:
            _invoke_codex([script], "", None, script)
        self.assertEqual(cm.exception.stderr, b"oops\n")


if __name__ == "__main__":
    unittest.main()
