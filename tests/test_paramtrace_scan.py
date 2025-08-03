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

    def test_gitlab_method_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample_dir = os.path.join(
                tmp, "gitlablib_paramtrace", "gitlab", "lib", "api"
            )
            os.makedirs(sample_dir)
            sample = os.path.join(sample_dir, "foo.rb-codex")
            content = """```json
{
  \"x -> y\": {
    \"user_controlled\": \"yes\",
    \"trace\": \"tr\",
    \"evidence\": \"ev\"
  }
}
```"""
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(content)

            gl_findings = {"Foo#bar": {"files": ["gitlab/lib/api/foo.rb"]}}
            with open(
                os.path.join(tmp, "gitlab_findings.json"), "w", encoding="utf-8"
            ) as fh:
                json.dump(gl_findings, fh)

            out = subprocess.check_output([
                sys.executable,
                "dispatch.py",
                "--scan-paramtrace",
                tmp,
            ])
            data = json.loads(out.decode())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["method"], "Foo#bar")

