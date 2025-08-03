import os
import sys
import json
import tempfile
import unittest
import unittest.mock
import subprocess

import codexdispatch as dispatch


class TestParamtraceArgs(unittest.TestCase):
    def test_parse_scan_paramtrace(self):
        argv = ["dispatch.py", "--scan-paramtrace", "dir"]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.scan_paramtrace, "dir")


class TestParamtraceScan(unittest.TestCase):
    def test_scan_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, "file-codex")
            content = """```json
{
  \"a -> b\": {
    \"user_controlled\": \"yes\",
    \"evidence\": \"ev\",
    \"trace\": \"tr\"
  },
  \"c -> d\": {
    \"user_controlled\": \"no\"
  }
}
```"""
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(content)
            out = subprocess.check_output([
                sys.executable,
                "dispatch.py",
                "--scan-paramtrace",
                tmp,
            ])
            data = json.loads(out.decode())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["file"], sample)
            self.assertEqual(data[0]["param"], "a -> b")
            self.assertEqual(data[0]["trace"], "tr")

