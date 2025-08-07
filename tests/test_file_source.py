import json
import os
import tempfile
import threading
import unittest
import unittest.mock as mock
from types import SimpleNamespace

import phase_mode.dispatcher as dispatcher


class TestFileSource(unittest.TestCase):
    def test_process_finding_includes_file_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.py")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("print('hi')\n")

            phase1 = os.path.join(tmp, "phase1")
            final = os.path.join(tmp, "final")
            cache = os.path.join(tmp, "cache")
            os.makedirs(phase1, exist_ok=True)
            os.makedirs(final, exist_ok=True)
            os.makedirs(cache, exist_ok=True)

            finding = {"id": "v1", "file_path": src, "severity": "low"}
            name = "finding.json"
            with open(os.path.join(phase1, name), "w", encoding="utf-8") as fh:
                json.dump(finding, fh)

            args = SimpleNamespace(
                phase1_dir=phase1,
                final_dir=final,
                cache_dir=cache,
                min_severity=None,
                phase_root=None,
                orchestrator_template_text="orch",
                max_inquiries=1,
                timeout=10,
                codex_bin="codex",
                output_dir=tmp,
            )

            with mock.patch(
                "phase_mode.dispatcher.call_orchestrator",
                return_value={"conclusion": "valid", "summary": "ok"},
            ):
                dispatcher.process_finding(
                    name, args, orchestrator_env=None, semaphore=threading.Semaphore()
                )

            final_path = os.path.join(final, name)
            with open(final_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            self.assertIn("file_source", data)
            self.assertEqual(data["file_source"]["path"], os.path.abspath(src))
            self.assertTrue(data["file_source"]["contents"])


if __name__ == "__main__":
    unittest.main()

