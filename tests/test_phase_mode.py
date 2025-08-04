import os
import sys
import json
import hashlib
import tempfile
import unittest
import unittest.mock as mock

import codexdispatch as dispatch
import codexdispatch.dispatcher as dispatcher


class TestPhaseModeWorkflow(unittest.TestCase):
    def test_phase_mode_split_and_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("source")

            findings = {
                "vulnerabilities": [
                    {"id": "v1", "file_path": src, "severity": "low"},
                    {"id": "v2", "file_path": src, "severity": "high"},
                ]
            }
            findings_path = os.path.join(tmp, "findings.json")
            with open(findings_path, "w", encoding="utf-8") as fh:
                json.dump(findings, fh)

            listfile = os.path.join(tmp, "list.txt")
            with open(listfile, "w", encoding="utf-8") as fh:
                fh.write(findings_path + "\n")

            orch = os.path.join(tmp, "orch.txt")
            with open(orch, "w", encoding="utf-8") as fh:
                fh.write("orch")

            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)

            argv = [
                "dispatch.py",
                "--phase-mode",
                "--orchestrator-template",
                orch,
                "--findings-list",
                listfile,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            with mock.patch.object(sys, "argv", argv):
                args = dispatch.parse_args()

            captured_cmds: list[list[str]] = []

            def fake_invoke(cmd, prompt, timeout, path):
                captured_cmds.append(list(cmd))
                out = cmd[cmd.index("--output-last-message") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("resp")

            orch_responses = [
                {"conclusion": "valid", "summary": "ok"},
                {"inquiry": "why"},
                {"conclusion": "invalid", "summary": "no"},
            ]

            prompts: list[str] = []

            def fake_orchestrator(prompt, env=None):
                prompts.append(prompt)
                return orch_responses.pop(0)

            with mock.patch("codexdispatch.dispatcher.find_codex_bin", return_value="codex"), \
                mock.patch("codexdispatch.dispatcher._invoke_codex", side_effect=fake_invoke), \
                mock.patch("codexdispatch.dispatcher.call_orchestrator", side_effect=fake_orchestrator):
                dispatcher._run_phase_mode(args)

            phase1_dir = os.path.join(outdir, "phase_1")
            v1_file = os.path.join(phase1_dir, f"v1_{os.path.basename(src)}.json")
            v2_file = os.path.join(phase1_dir, f"v2_{os.path.basename(src)}.json")
            self.assertTrue(os.path.exists(v1_file))
            self.assertTrue(os.path.exists(v2_file))

            cache_dir = os.path.join(tmp, "cache")
            key = hashlib.sha256(f"v2:{src}".encode()).hexdigest()
            self.assertTrue(os.path.exists(os.path.join(cache_dir, f"{key}.json")))

            verdicts_path = os.path.join(outdir, "final", "verdicts.json")
            with open(verdicts_path, "r", encoding="utf-8") as vf:
                verdicts = json.load(vf)
            self.assertEqual(verdicts["v1"]["conclusion"], "valid")
            self.assertEqual(verdicts["v2"]["conclusion"], "invalid")

            phase2 = os.path.join(outdir, "phase_2", os.path.basename(v2_file))
            self.assertTrue(os.path.exists(phase2))

            self.assertIn("-C", captured_cmds[0])
            self.assertEqual(
                captured_cmds[0][captured_cmds[0].index("-C") + 1], os.path.dirname(src)
            )

            self.assertIn("source", prompts[0])


    def test_phase_mode_phase_root_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, "pkg"), exist_ok=True)
            src_rel = os.path.join("pkg", "src.txt")
            src = os.path.join(repo, src_rel)
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("hello")

            findings = {
                "vulnerabilities": [
                    {"id": "v1", "file_path": src_rel, "severity": "low"}
                ]
            }
            findings_path = os.path.join(tmp, "findings.json")
            with open(findings_path, "w", encoding="utf-8") as fh:
                json.dump(findings, fh)

            listfile = os.path.join(tmp, "list.txt")
            with open(listfile, "w", encoding="utf-8") as fh:
                fh.write(findings_path + "\n")

            orch = os.path.join(tmp, "orch.txt")
            with open(orch, "w", encoding="utf-8") as fh:
                fh.write("orch")

            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)

            argv = [
                "dispatch.py",
                "--phase-mode",
                "--orchestrator-template",
                orch,
                "--findings-list",
                listfile,
                "--phase-root",
                repo,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            with mock.patch.object(sys, "argv", argv):
                args = dispatch.parse_args()

            captured_cmds: list[list[str]] = []
            prompts: list[str] = []

            def fake_invoke(cmd, prompt, timeout, path):
                captured_cmds.append(list(cmd))
                out = cmd[cmd.index("--output-last-message") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("resp")

            orch_responses = [{"inquiry": "why"}, {"conclusion": "valid"}]

            def fake_orchestrator(prompt, env=None):
                prompts.append(prompt)
                return orch_responses.pop(0)

            with mock.patch("codexdispatch.dispatcher.find_codex_bin", return_value="codex"), \
                mock.patch("codexdispatch.dispatcher._invoke_codex", side_effect=fake_invoke), \
                mock.patch("codexdispatch.dispatcher.call_orchestrator", side_effect=fake_orchestrator):
                dispatcher._run_phase_mode(args)

            self.assertIn("hello", prompts[0])
            self.assertEqual(
                captured_cmds[0][captured_cmds[0].index("-C") + 1],
                os.path.join(repo, "pkg"),
            )


if __name__ == "__main__":
    unittest.main()

