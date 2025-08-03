import json
import os
import subprocess
import sys
import tempfile
import unittest


class TestCombineTraces(unittest.TestCase):
    def test_combined_output_contains_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = os.path.join(tmp, "base")
            os.makedirs(base_dir)
            # File referenced by paramtrace
            pt_file = os.path.join(base_dir, "sample.rb")
            with open(pt_file, "w", encoding="utf-8") as fh:
                fh.write("paramtrace source\n")

            # Paramtrace data
            paramtrace = [
                {
                    "file": "dummy",  # unused
                    "param": "p",
                    "trace": ["sample.rb:L1-2"],
                    "evidence": "ev",
                    "method": "Foo.bar",
                }
            ]
            pt_path = os.path.join(tmp, "paramtrace.json")
            with open(pt_path, "w", encoding="utf-8") as fh:
                json.dump(paramtrace, fh)

            # File referenced by newfindings
            nf_file = os.path.join(tmp, "nf.rb")
            with open(nf_file, "w", encoding="utf-8") as fh:
                fh.write("newfinding source\n")

            nf_entry = {"method": "Foo.bar", "trace": [nf_file]}
            nf_path = os.path.join(tmp, "newfindings.json")
            with open(nf_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(nf_entry) + "\n")

            out_dir = os.path.join(tmp, "out")

            subprocess.check_call(
                [
                    sys.executable,
                    os.path.join(os.getcwd(), "combine_traces.py"),
                    "--paramtrace",
                    pt_path,
                    "--newfindings",
                    nf_path,
                    "--base-dir",
                    base_dir,
                    "--output-dir",
                    out_dir,
                ]
            )

            out_file = os.path.join(out_dir, "Foo_bar.txt")
            self.assertTrue(os.path.exists(out_file))
            with open(out_file, "r", encoding="utf-8") as fh:
                data = fh.read()
            self.assertIn("paramtrace source", data)
            self.assertIn("newfinding source", data)


if __name__ == "__main__":
    unittest.main()
