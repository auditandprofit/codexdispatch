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
            slug = "Sample"
            sample_dir = os.path.join(tmp, slug)
            os.makedirs(sample_dir)
            sample = os.path.join(sample_dir, "file.rb-codex")
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

            findings = {
                "Sample": {
                    "finding": {"method": "x"},
                    "files": ["file.rb"],
                }
            }
            with open(os.path.join(tmp, "findings.json"), "w", encoding="utf-8") as fh:
                json.dump(findings, fh)

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
            self.assertEqual(data[0]["finding"], {"method": "x"})

