import os
import sys
import json
import tempfile
import unittest
import unittest.mock as mock

import compat_mode


class TestCompatMode(unittest.TestCase):
    def test_correlate_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings_dir = os.path.join(tmp, "findings")
            vulns_dir = os.path.join(tmp, "vulns")
            repo = os.path.join(tmp, "repo")
            outdir = os.path.join(tmp, "out")
            os.makedirs(findings_dir)
            os.makedirs(vulns_dir)
            os.makedirs(repo)
            os.makedirs(outdir)

            # source file
            src_rel = os.path.join("pkg", "src.txt")
            src_abs = os.path.join(repo, src_rel)
            os.makedirs(os.path.dirname(src_abs), exist_ok=True)
            with open(src_abs, "w", encoding="utf-8") as fh:
                fh.write("hello")

            vuln_obj = {"id": "VULN-001", "file_path": src_rel}
            vuln_path = os.path.join(vulns_dir, "VULN-001.json")
            with open(vuln_path, "w", encoding="utf-8") as fh:
                json.dump(vuln_obj, fh)

            finding_obj = {"id": "VULN-001", "summary": "issue"}
            finding_path = os.path.join(findings_dir, "VULN-001_pkg_src.txt.json")
            with open(finding_path, "w", encoding="utf-8") as fh:
                json.dump(finding_obj, fh)

            argv = [
                "prog",
                "--findings-dir",
                findings_dir,
                "--vuln-dir",
                vulns_dir,
                "--phase-root",
                repo,
                "-o",
                outdir,
            ]
            with mock.patch.object(sys, "argv", argv):
                args = compat_mode.parse_args()
            compat_mode.run_compat_mode(args)

            out_file = os.path.join(outdir, "VULN-001_pkg_src.txt.json")
            with open(out_file, "r", encoding="utf-8") as fh:
                out_obj = json.load(fh)
            self.assertEqual(out_obj["vulnerability"]["id"], "VULN-001")
            self.assertEqual(out_obj["vulnerability"]["file_path"], src_abs)
            self.assertIn("hello", out_obj["source"])
            self.assertEqual(out_obj["summary"]["summary"], "issue")

            summary_file = os.path.join(outdir, "findings_summary.json")
            with open(summary_file, "r", encoding="utf-8") as fh:
                summary = json.load(fh)
            self.assertIn("VULN-001", summary)


if __name__ == "__main__":
    unittest.main()
