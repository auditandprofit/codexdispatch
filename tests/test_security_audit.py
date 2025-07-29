import os
import sys
import subprocess
import tempfile
import json
import unittest

import codexdispatch as dispatch


class TestParseCodexJson(unittest.TestCase):
    def test_parse_good(self):
        blob = "header\n" + json.dumps({"notes": [], "followup": []})
        res = dispatch.parse_codex_json(blob)
        self.assertIn("notes", res)
        self.assertIn("followup", res)

    def test_parse_bad(self):
        res = dispatch.parse_codex_json("not-json")
        self.assertEqual(res, {"notes": [], "followup": []})


class TestAuditBfs(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.codex = os.path.join(self.tmpdir.name, "fake_codex.py")
        with open(self.codex, "w", encoding="utf-8") as f:
            f.write(
                """#!/usr/bin/env python3
import os, sys, json
out = sys.argv[sys.argv.index('--output-last-message') + 1]
data = sys.stdin.read()
bpath = os.environ['B_PATH']
res = {}
if 'a.txt' in out:
    res = {'notes': [], 'followup': [bpath]}
elif 'b.txt' in out:
    res = {'notes': [], 'followup': ['LOOK']}
else:
    res = {'notes': [], 'followup': []}
with open(out, 'w') as fh:
    fh.write('x\\n' + json.dumps(res))
"""
            )
        os.chmod(self.codex, 0o755)

    def run_audit(self, extra_env=None):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        subprocess.check_call([sys.executable, "dispatch.py"] + self.args, env=env)

    def test_bfs_dedup(self):
        tree = os.path.join(self.tmpdir.name, "tree")
        os.mkdir(tree)
        a = os.path.join(tree, "a.txt")
        b = os.path.join(tree, "b.txt")
        for p in (a, b):
            with open(p, "w", encoding="utf-8") as f:
                f.write(p)
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)
        self.args = [
            "dummy",  # placeholder for template
            "--tree-dirs", tree,
            "-o", outdir,
            "-j", "1",
            "--codex-bin", self.codex,
            "--security-audit",
            "--audit-focus", "TEST FOCUS",
            "--depth", "2",
        ]
        self.run_audit({"B_PATH": b})
        d0_a = os.path.join(
            outdir,
            "security",
            "depth_0",
            os.path.join("1_" + os.path.basename(tree), "a.txt") + "-audit.json",
        )
        d0_b = os.path.join(
            outdir,
            "security",
            "depth_0",
            os.path.join("1_" + os.path.basename(tree), "b.txt") + "-audit.json",
        )
        self.assertTrue(os.path.exists(d0_a))
        self.assertTrue(os.path.exists(d0_b))
        d1 = os.path.join(outdir, "security", "depth_1")
        self.assertTrue(os.path.exists(os.path.join(d1, "LOOK-audit.json")))
        with open(os.path.join(outdir, "security_summary.json"), "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(set(summary.keys()), {os.path.abspath(a), os.path.abspath(b), "LOOK"})
        self.assertEqual(summary[os.path.abspath(b)]["depth"], 0)

    def test_audit_root_resolves_relative(self):
        tree = os.path.join(self.tmpdir.name, "tree2")
        os.makedirs(os.path.join(tree, "sub"))
        a = os.path.join(tree, "a.txt")
        b = os.path.join(tree, "sub", "b.txt")
        for p in (a, b):
            with open(p, "w", encoding="utf-8") as f:
                f.write(p)
        lst = os.path.join(self.tmpdir.name, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write(a + "\n")
        outdir = os.path.join(self.tmpdir.name, "out2")
        os.mkdir(outdir)
        self.args = [
            "dummy",
            "--file-list",
            lst,
            "-o",
            outdir,
            "-j",
            "1",
            "--codex-bin",
            self.codex,
            "--security-audit",
            "--audit-focus",
            "TEST",
            "--depth",
            "2",
            "--audit-root",
            tree,
            "-C",
            tree,
        ]
        self.run_audit({"B_PATH": "sub/b.txt"})
        with open(os.path.join(outdir, "security_summary.json"), "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertIn(os.path.abspath(b), summary)
        self.assertEqual(summary[os.path.abspath(b)]["depth"], 1)


class TestMockAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def run_audit(self):
        subprocess.check_call([sys.executable, "dispatch.py"] + self.args)

    def test_mock_mode_generates_outputs(self):
        root = os.path.join(self.tmpdir.name, "proj")
        os.mkdir(root)
        for idx in range(3):
            with open(os.path.join(root, f"f{idx}.txt"), "w", encoding="utf-8") as f:
                f.write("x")
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)
        self.args = [
            "dummy",
            "--tree-dirs",
            root,
            "-o",
            outdir,
            "-j",
            "1",
            "--security-audit",
            "--audit-focus",
            "TEST",
            "--depth",
            "2",
            "--mock-audit",
        ]
        self.run_audit()
        depth0 = os.path.join(outdir, "security", "depth_0")
        found = False
        for rootdir, _, files in os.walk(depth0):
            if any(f.endswith("-audit.json") for f in files):
                found = True
                break
        self.assertTrue(found)
        with open(os.path.join(outdir, "security_summary.json"), "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertTrue(len(summary) >= 3)


if __name__ == "__main__":
    unittest.main()
