import os
import sys
import json
import tempfile
import unittest
import unittest.mock as mock
import hashlib

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

            captured_cmds: list[list[str]] = []

            def fake_invoke(cmd, prompt, timeout, path):
                captured_cmds.append(list(cmd))
                out = cmd[cmd.index("--output-last-message") + 1]
                if "phase_1" in out:
                    with open(out, "w", encoding="utf-8") as fh:
                        fh.write(phase1_output)
                else:
                    with open(out, "w", encoding="utf-8") as fh:
                        fh.write(inquiry_output)

            orch_responses = [{"inquiry": "why?"}, {"conclusion": "valid", "summary": "done"}]

            def fake_orchestrator(prompt):
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
            self.assertIn("workdirs.json", cache_files)
            hash_file = [f for f in cache_files if f != "workdirs.json"][0]
            with open(os.path.join(cache_dir, hash_file), "r", encoding="utf-8") as cf:
                data = json.load(cf)
            self.assertEqual(data["status"], "concluded")
            self.assertEqual(len(data["context"]), 1)
            with open(os.path.join(cache_dir, "workdirs.json"), "r", encoding="utf-8") as wf:
                wd_map = json.load(wf)
            self.assertEqual(wd_map[phase1_path], os.path.dirname(src))
            phase2_meta = phase2_path + ".meta"
            self.assertTrue(os.path.exists(phase2_meta))
            with open(phase2_meta, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            self.assertEqual(meta["inquiry"], "why?")
            self.assertEqual(meta["response"], inquiry_output)
            self.assertEqual(len(captured_cmds), 2)
            second_cmd = captured_cmds[1]
            self.assertIn("-C", second_cmd)
            self.assertEqual(second_cmd[second_cmd.index("-C") + 1], os.path.dirname(src))

    def test_presupplied_findings_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "f1.json")
            f2 = os.path.join(tmp, "f2.json")
            for path, val in ((f1, "a"), (f2, "b")):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"finding": val}))
            listfile = os.path.join(tmp, "findings.txt")
            with open(listfile, "w", encoding="utf-8") as fh:
                fh.write(f1 + "\n")
                fh.write("# comment\n")
                fh.write(f2 + "\n")
                fh.write(f1 + "\n")
            orch = os.path.join(tmp, "orch.txt")
            with open(orch, "w", encoding="utf-8") as fh:
                fh.write("orch")
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)
            workdir = os.path.join(tmp, "repo")
            os.mkdir(workdir)
            argv = [
                "dispatch.py",
                "--phase-mode",
                "--orchestrator-template",
                orch,
                "--findings-list",
                listfile,
                "--findings-workdir",
                workdir,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            with mock.patch.object(sys, "argv", argv):
                args = dispatch.parse_args()

            def fake_find_codex_bin(path):
                return "codex"

            captured_cmds: list[list[str]] = []

            def fake_invoke(cmd, prompt, timeout, path):
                captured_cmds.append(list(cmd))
                out = cmd[cmd.index("--output-last-message") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("resp")

            orch_responses = [
                {"conclusion": "valid", "summary": "done"},
                {"inquiry": "why?"},
                {"conclusion": "invalid", "summary": "oops"},
            ]

            def fake_orchestrator(prompt):
                return orch_responses.pop(0)

            with mock.patch("codexdispatch.dispatcher.find_codex_bin", fake_find_codex_bin), \
                mock.patch("codexdispatch.dispatcher._invoke_codex", fake_invoke), \
                mock.patch("codexdispatch.dispatcher.call_orchestrator", fake_orchestrator):
                dispatcher._run_phase_mode(args)

            phase1_dir = os.path.join(outdir, "phase_1")
            self.assertTrue(os.path.exists(os.path.join(phase1_dir, os.path.basename(f1))))
            self.assertTrue(os.path.exists(os.path.join(phase1_dir, os.path.basename(f2))))
            final_dir = os.path.join(outdir, "final")
            self.assertTrue(os.path.exists(os.path.join(final_dir, os.path.basename(f1))))
            self.assertTrue(os.path.exists(os.path.join(final_dir, os.path.basename(f2))))
            phase2_meta = os.path.join(outdir, "phase_2", os.path.basename(f2)) + ".meta"
            self.assertTrue(os.path.exists(phase2_meta))
            with open(phase2_meta, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            self.assertEqual(meta["response"], "resp")
            cache_dir = os.path.join(tmp, "cache")
            with open(os.path.join(cache_dir, "workdirs.json"), "r", encoding="utf-8") as wf:
                wd_map = json.load(wf)
            self.assertEqual(
                wd_map[os.path.join(phase1_dir, os.path.basename(f1))], workdir
            )
            self.assertEqual(len(captured_cmds), 1)

    def test_cache_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            finding = os.path.join(tmp, "f.json")
            with open(finding, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"finding": "x"}))
            listfile = os.path.join(tmp, "findings.txt")
            with open(listfile, "w", encoding="utf-8") as fh:
                fh.write(finding + "\n")
            orch = os.path.join(tmp, "orch.txt")
            with open(orch, "w", encoding="utf-8") as fh:
                fh.write("orch")
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)
            workdir = os.path.join(tmp, "repo")
            os.mkdir(workdir)
            argv = [
                "dispatch.py",
                "--phase-mode",
                "--orchestrator-template",
                orch,
                "--findings-list",
                listfile,
                "--findings-workdir",
                workdir,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            with mock.patch.object(sys, "argv", argv):
                args = dispatch.parse_args()

            def fake_find_codex_bin(path):
                return "codex"

            captured_cmds: list[list[str]] = []

            def fake_invoke(cmd, prompt, timeout, path):
                captured_cmds.append(list(cmd))
                out = cmd[cmd.index("--output-last-message") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("resp")

            orch_responses = [{"inquiry": "why?"}]

            def first_orchestrator(prompt):
                if orch_responses:
                    return orch_responses.pop(0)
                raise IndexError

            try:
                with mock.patch("codexdispatch.dispatcher.find_codex_bin", fake_find_codex_bin), \
                    mock.patch("codexdispatch.dispatcher._invoke_codex", fake_invoke), \
                    mock.patch("codexdispatch.dispatcher.call_orchestrator", first_orchestrator):
                    dispatcher._run_phase_mode(args)
            except IndexError:
                pass

            phase1_file = os.path.join(outdir, "phase_1", os.path.basename(finding))
            cache_dir = os.path.join(tmp, "cache")
            cache_key = hashlib.sha256(phase1_file.encode()).hexdigest()
            cache_path = os.path.join(cache_dir, f"{cache_key}.json")
            with open(cache_path, "r", encoding="utf-8") as cf:
                data = json.load(cf)
            self.assertEqual(data["status"], "open")
            self.assertEqual(len(captured_cmds), 1)

            orch_responses2 = [{"conclusion": "valid", "summary": "done"}]

            def second_orchestrator(prompt):
                return orch_responses2.pop(0)

            captured_cmds2: list[list[str]] = []

            def fake_invoke2(cmd, prompt, timeout, path):
                captured_cmds2.append(list(cmd))

            with mock.patch("codexdispatch.dispatcher.find_codex_bin", fake_find_codex_bin), \
                mock.patch("codexdispatch.dispatcher._invoke_codex", fake_invoke2), \
                mock.patch("codexdispatch.dispatcher.call_orchestrator", second_orchestrator):
                dispatcher._run_phase_mode(args)

            final_file = os.path.join(outdir, "final", os.path.basename(finding))
            self.assertTrue(os.path.exists(final_file))
            with open(cache_path, "r", encoding="utf-8") as cf:
                data = json.load(cf)
            self.assertEqual(data["status"], "concluded")
            self.assertEqual(len(captured_cmds2), 0)


if __name__ == "__main__":
    unittest.main()
