import os
import sys
import json
import tempfile
import unittest
import unittest.mock as mock

import codexdispatch as dispatch
import codexdispatch.dispatcher as dispatcher


class TestPhaseModeWorkflow(unittest.TestCase):
    def test_phase_mode_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("data")
            listfile = os.path.join(tmp, "list.txt")
            with open(listfile, "w", encoding="utf-8") as fh:
                fh.write(src + "\n")
            audit = os.path.join(tmp, "audit.txt")
            with open(audit, "w", encoding="utf-8") as fh:
                fh.write("audit")
            orch = os.path.join(tmp, "orch.txt")
            with open(orch, "w", encoding="utf-8") as fh:
                fh.write("orch")
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)
            argv = [
                "dispatch.py",
                "--phase-mode",
                "--audit-template",
                audit,
                "--orchestrator-template",
                orch,
                "--file-list",
                listfile,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            with mock.patch.object(sys, "argv", argv):
                args = dispatch.parse_args()

            def fake_find_codex_bin(path):
                return "codex"

            phase1_output = '{"finding": "x"}'
            inquiry_output = "resp"

            def fake_invoke(cmd, prompt, timeout, path):
                out = cmd[cmd.index("--output-last-message") + 1]
                if "phase_1" in out:
                    with open(out, "w", encoding="utf-8") as fh:
                        fh.write(phase1_output)
                else:
                    with open(out, "w", encoding="utf-8") as fh:
                        fh.write(inquiry_output)

            orch_responses = [{"inquiry": "why?"}, {"conclusion": "valid"}]

            def fake_orchestrator(template, context):
                return orch_responses.pop(0)

            with mock.patch("codexdispatch.dispatcher.find_codex_bin", fake_find_codex_bin), \
                mock.patch("codexdispatch.dispatcher._invoke_codex", fake_invoke), \
                mock.patch("codexdispatch.dispatcher.call_orchestrator", fake_orchestrator):
                dispatcher._run_phase_mode(args)

            rel_path = os.path.splitdrive(os.path.abspath(src))[1].lstrip(os.sep) + "-codex"
            phase1_path = os.path.join(outdir, "phase_1", rel_path)
            self.assertTrue(os.path.exists(phase1_path))
            phase2_path = os.path.join(outdir, "phase_2", rel_path)
            self.assertTrue(os.path.exists(phase2_path))
            final_path = os.path.join(outdir, "final", rel_path)
            self.assertTrue(os.path.exists(final_path))
            cache_dir = os.path.join(tmp, "cache")
            self.assertTrue(os.path.isdir(cache_dir))
            cache_files = os.listdir(cache_dir)
            self.assertEqual(len(cache_files), 1)
            with open(os.path.join(cache_dir, cache_files[0]), "r", encoding="utf-8") as cf:
                data = json.load(cf)
            self.assertEqual(data["status"], "concluded")
            self.assertEqual(len(data["context"]), 1)


if __name__ == "__main__":
    unittest.main()
