import os
import sys
import subprocess
import tempfile
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import dispatch

class TestParseCodexJson(unittest.TestCase):
    def test_parse_good(self):
        blob = "header\n" + json.dumps({"findings": [], "leads": []})
        res = dispatch.parse_codex_json(blob)
        self.assertIn("findings", res)
        self.assertIn("leads", res)

    def test_parse_bad(self):
        res = dispatch.parse_codex_json("not-json")
        self.assertEqual(res, {"findings": [], "leads": []})

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
    res = {'findings': [], 'leads': [{'desc':'b','path': bpath}]}
elif 'b.txt' in out:
    res = {'findings': [], 'leads': [{'desc':'h','path': None}]}
else:
    res = {'findings': [], 'leads': []}
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
        d0_a = os.path.join(outdir, "security", "depth_0", os.path.join("1_" + os.path.basename(tree), "a.txt") + "-audit.json")
        d0_b = os.path.join(outdir, "security", "depth_0", os.path.join("1_" + os.path.basename(tree), "b.txt") + "-audit.json")
        self.assertTrue(os.path.exists(d0_a))
        self.assertTrue(os.path.exists(d0_b))
        d1 = os.path.join(outdir, "security", "depth_1")
        self.assertFalse(os.path.exists(d1) and os.listdir(d1))
        with open(os.path.join(outdir, "security_summary.json"), "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(set(summary.keys()), {os.path.abspath(a), os.path.abspath(b)})
        self.assertEqual(summary[os.path.abspath(b)]["depth"], 0)

if __name__ == "__main__":
    unittest.main()
