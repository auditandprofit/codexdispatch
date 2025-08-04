import os
import sys
import subprocess
import tempfile
import json
import re

import unittest
import unittest.mock

import codexdispatch as dispatch


class TestParseArgs(unittest.TestCase):
    def test_parse_data_dir(self):
        argv = ["dispatch.py", "tmpl", "--data-dir", "d", "-o", "out", "-j", "1"]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.data_dir, "d")
        self.assertIsNone(args.tree_dirs)
        self.assertIsNone(args.file_list)

    def test_parse_passes_map(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--tree-dirs",
            "src",
            "-o",
            "out",
            "-j",
            "1",
            "--passes",
            "3",
            "--map-name",
            "map.json",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.passes, 3)
        self.assertEqual(args.map_name, "map.json")

    def test_parse_tree_dirs(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--tree-dirs",
            "a",
            "b",
            "-o",
            "out",
            "-j",
            "2",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.tree_dirs, ["a", "b"])
        self.assertIsNone(args.data_dir)
        self.assertIsNone(args.file_list)

    def test_parse_file_list(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--file-list",
            "list.txt",
            "-o",
            "out",
            "-j",
            "3",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.file_list, "list.txt")
        self.assertIsNone(args.data_dir)
        self.assertIsNone(args.tree_dirs)
        self.assertTrue(args.prepend_path)
        self.assertFalse(args.per_file_workdir)

    def test_parse_per_file_workdir(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--file-list",
            "list.txt",
            "-o",
            "out",
            "-j",
            "1",
            "--per-file-workdir",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertTrue(args.per_file_workdir)

    def test_parse_no_prepend_path(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--file-list",
            "list.txt",
            "-o",
            "out",
            "-j",
            "1",
            "--no-prepend-path",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertFalse(args.prepend_path)

    def test_parse_findings_json(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--findings-json",
            "find.json",
            "-o",
            "out",
            "-j",
            "1",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertEqual(args.findings_json, "find.json")
        self.assertIsNone(args.work_dir)

    def test_findings_rejects_workdir(self):
        argv = [
            "dispatch.py",
            "tmpl",
            "--findings-json",
            "find.json",
            "-o",
            "out",
            "-j",
            "1",
            "-C",
            "wd",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit):
                dispatch.parse_args()

    def test_parse_phase_mode(self):
        argv = [
            "dispatch.py",
            "--phase-mode",
            "--phase-templates",
            "tmpldir",
            "--phase-workdir",
            "wd",
            "--initial-files",
            "files.txt",
            "-o",
            "out",
            "-j",
            "2",
        ]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = dispatch.parse_args()
        self.assertTrue(args.phase_mode)
        self.assertEqual(args.phase_templates, "tmpldir")
        self.assertEqual(args.phase_workdir, "wd")
        self.assertEqual(args.initial_files, "files.txt")


class TestWorkDirSelection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(self.template, "w", encoding="utf-8") as f:
            f.write("T")
        self.subdir = os.path.join(self.tmpdir.name, "sub")
        os.mkdir(self.subdir)
        self.file = os.path.join(self.subdir, "file.txt")
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("X")
        self.listfile = os.path.join(self.tmpdir.name, "list.txt")
        with open(self.listfile, "w", encoding="utf-8") as f:
            f.write(self.file + "\n")
        self.outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(self.outdir)

    def _dispatch_and_capture(self, extra_args):
        argv = [
            "dispatch.py",
            self.template,
            "--file-list",
            self.listfile,
            "-o",
            self.outdir,
            "-j",
            "1",
        ] + extra_args
        captured = {}

        def fake_invoke(cmd, prompt, timeout, path):
            captured[path] = cmd[cmd.index("-C") + 1]

        with unittest.mock.patch.object(sys, "argv", argv), \
            unittest.mock.patch("codexdispatch.dispatcher.find_codex_bin", return_value="codex"), \
            unittest.mock.patch("codexdispatch.dispatcher._invoke_codex", side_effect=fake_invoke):
            dispatch.main()
        return captured[self.file]

    def test_workdir_combinations(self):
        cwd = os.getcwd()
        self.assertEqual(self._dispatch_and_capture([]), cwd)
        self.assertEqual(
            self._dispatch_and_capture(["-C", self.tmpdir.name]),
            self.tmpdir.name,
        )
        self.assertEqual(
            self._dispatch_and_capture(["--per-file-workdir"]),
            self.subdir,
        )
        self.assertEqual(
            self._dispatch_and_capture(["-C", self.tmpdir.name, "--per-file-workdir"]),
            self.subdir,
        )


class PerFileWorkDirIntegration(unittest.TestCase):
    def test_per_file_workdir_two_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = os.path.join(tmp, "tmpl.txt")
            with open(template, "w", encoding="utf-8") as f:
                f.write("T")
            dir_a = os.path.join(tmp, "a")
            dir_b = os.path.join(tmp, "b")
            os.mkdir(dir_a)
            os.mkdir(dir_b)
            file_a = os.path.join(dir_a, "a.txt")
            file_b = os.path.join(dir_b, "b.txt")
            with open(file_a, "w", encoding="utf-8") as f:
                f.write("A")
            with open(file_b, "w", encoding="utf-8") as f:
                f.write("B")
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                f.write(file_a + "\n" + file_b + "\n")
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)

            argv = [
                "dispatch.py",
                template,
                "--file-list",
                lst,
                "-o",
                outdir,
                "-j",
                "2",
                "--per-file-workdir",
            ]
            captured: dict[str, str] = {}

            def fake_invoke(cmd, prompt, timeout, path):
                captured[path] = cmd[cmd.index("-C") + 1]

            with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch("codexdispatch.dispatcher.find_codex_bin", return_value="codex"), \
                unittest.mock.patch("codexdispatch.dispatcher._invoke_codex", side_effect=fake_invoke):
                dispatch.main()

            self.assertEqual(captured[file_a], dir_a)
            self.assertEqual(captured[file_b], dir_b)


class PhaseModeIntegration(unittest.TestCase):
    def test_phase_mode_two_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpl_dir = os.path.join(tmp, "tmpl")
            os.mkdir(tmpl_dir)
            with open(os.path.join(tmpl_dir, "1"), "w", encoding="utf-8") as f:
                f.write("T1")
            with open(os.path.join(tmpl_dir, "2"), "w", encoding="utf-8") as f:
                f.write("T2")
            file_a = os.path.join(tmp, "a.txt")
            with open(file_a, "w", encoding="utf-8") as f:
                f.write("A")
            subdir = os.path.join(tmp, "sub")
            os.mkdir(subdir)
            file_b = os.path.join(subdir, "b.txt")
            with open(file_b, "w", encoding="utf-8") as f:
                f.write("B")
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                f.write(file_a + "\n" + subdir + "\n")
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)
            phase_wd = os.path.join(tmp, "wd")
            os.mkdir(phase_wd)

            argv = [
                "dispatch.py",
                "--phase-mode",
                "--phase-templates",
                tmpl_dir,
                "--phase-workdir",
                phase_wd,
                "--initial-files",
                lst,
                "-o",
                outdir,
                "-j",
                "1",
            ]
            captured: dict[str, str] = {}

            def fake_invoke(cmd, prompt, timeout, path):
                out = cmd[cmd.index("--output-last-message") + 1]
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("X")
                captured[path] = cmd[cmd.index("-C") + 1]

            with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch("codexdispatch.dispatcher.find_codex_bin", return_value="codex"), \
                unittest.mock.patch("codexdispatch.dispatcher._invoke_codex", side_effect=fake_invoke):
                dispatch.main()

            out_a = os.path.join(outdir, "1", os.path.abspath(file_a).lstrip(os.sep) + "-codex")
            out_b = os.path.join(outdir, "1", os.path.abspath(file_b).lstrip(os.sep) + "-codex")
            self.assertEqual(captured[file_a], os.path.dirname(file_a))
            self.assertEqual(captured[file_b], os.path.dirname(file_b))
            self.assertEqual(captured[out_a], phase_wd)
            self.assertEqual(captured[out_b], phase_wd)


class DispatchIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.workdir = os.path.join(self.tmpdir.name, "work")
        os.mkdir(self.workdir)
        self.codex = os.path.join(self.tmpdir.name, "fake_codex.py")
        with open(self.codex, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import sys, os, json\n"
                "out = sys.argv[sys.argv.index('--output-last-message') + 1]\n"
                "wdir = sys.argv[sys.argv.index('-C') + 1]\n"
                "data = sys.stdin.read()\n"
                "with open(out, 'w') as fh: fh.write(data)\n"
                "with open(out + '.workdir', 'w') as fh: fh.write(wdir)\n"
            )
        os.chmod(self.codex, 0o755)

    def run_dispatch(self, args, env=None):
        subprocess.check_call([sys.executable, "dispatch.py"] + args, env=env)

    def test_prompt_and_workdir_file_list(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("TEMPLATE")
        file1 = os.path.join(self.tmpdir.name, "input.txt")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("content")
        lst = os.path.join(self.tmpdir.name, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write(file1 + "\n")
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)

        self.run_dispatch([
            template,
            "--file-list",
            lst,
            "-o",
            outdir,
            "-j",
            "1",
            "-C",
            self.workdir,
            "--codex-bin",
            self.codex,
        ])

        out_file = os.path.join(outdir, os.path.relpath(file1) + "-codex")
        with open(out_file, "r", encoding="utf-8") as f:
            self.assertEqual(
                f.read(), f"TEMPLATE\n{os.path.abspath(file1)}\ncontent"
            )
        with open(out_file + ".workdir", "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), self.workdir)

    def test_file_list_resolves_path(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("TEMPLATE")
        dir_path = os.path.join(self.tmpdir.name, "dir")
        weird_dir = os.path.join(self.tmpdir.name, "weird")
        os.mkdir(dir_path)
        os.mkdir(weird_dir)
        file1 = os.path.join(dir_path, "input.txt")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("content")
        weird_path = os.path.join(weird_dir, "..", "dir", "input.txt")
        lst = os.path.join(self.tmpdir.name, "list2.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write(weird_path + "\n")
        outdir = os.path.join(self.tmpdir.name, "out2")
        os.mkdir(outdir)

        self.run_dispatch([
            template,
            "--file-list",
            lst,
            "-o",
            outdir,
            "-j",
            "1",
            "-C",
            self.workdir,
            "--codex-bin",
            self.codex,
        ])

        out_file = os.path.join(outdir, os.path.relpath(weird_path) + "-codex")
        with open(out_file, "r", encoding="utf-8") as f:
            self.assertEqual(
                f.read(), f"TEMPLATE\n{os.path.abspath(weird_path)}\ncontent"
            )

    def test_file_list_without_prepend_path(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("TEMPLATE")
        file1 = os.path.join(self.tmpdir.name, "input.txt")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("content")
        lst = os.path.join(self.tmpdir.name, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write(file1 + "\n")
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)

        self.run_dispatch([
            template,
            "--file-list",
            lst,
            "-o",
            outdir,
            "-j",
            "1",
            "-C",
            self.workdir,
            "--codex-bin",
            self.codex,
            "--no-prepend-path",
        ])

        out_file = os.path.join(outdir, os.path.relpath(file1) + "-codex")
        with open(out_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "TEMPLATE\ncontent")

    def test_output_collision_file_list(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("T")
        a_dir = os.path.join(self.tmpdir.name, "a")
        b_dir = os.path.join(self.tmpdir.name, "b")
        os.mkdir(a_dir)
        os.mkdir(b_dir)
        fa = os.path.join(a_dir, "dup.txt")
        fb = os.path.join(b_dir, "dup.txt")
        for p in (fa, fb):
            with open(p, "w", encoding="utf-8") as f:
                f.write(p)
        lst = os.path.join(self.tmpdir.name, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write(fa + "\n" + fb + "\n")
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)
        self.run_dispatch([
            template,
            "--file-list",
            lst,
            "-o",
            outdir,
            "-j",
            "1",
            "-C",
            self.workdir,
            "--codex-bin",
            self.codex,
        ])
        out_a = os.path.join(outdir, os.path.relpath(fa) + "-codex")
        out_b = os.path.join(outdir, os.path.relpath(fb) + "-codex")
        self.assertTrue(os.path.exists(out_a))
        self.assertTrue(os.path.exists(out_b))

    def test_gitlab_findings_mode(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("TEMPLATE")
        src_file = os.path.join(self.tmpdir.name, "src.txt")
        with open(src_file, "w", encoding="utf-8") as f:
            f.write("SRC")
        findings = {
            "Some::Key": {"finding": {"method": "m"}, "files": ["/orig/src.txt"]}
        }
        findings_path = os.path.join(self.tmpdir.name, "findings.json")
        with open(findings_path, "w", encoding="utf-8") as jf:
            json.dump(findings, jf)
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)

        self.run_dispatch([
            template,
            "--findings-json",
            findings_path,
            "--relative-dir",
            self.tmpdir.name,
            "-o",
            outdir,
            "-j",
            "1",
            "--codex-bin",
            self.codex,
        ])

        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", "Some::Key")[:50]
        out_file = os.path.join(outdir, slug, "src.txt-codex")
        with open(out_file, "r", encoding="utf-8") as f:
            self.assertEqual(
                f.read(),
                f"TEMPLATE\n{{\n  \"method\": \"m\"\n}}\n{os.path.abspath(src_file)}\nSRC",
            )
        with open(out_file + ".workdir", "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), os.path.dirname(src_file))

    def test_multi_pass_tree_dirs(self):
        template = os.path.join(self.tmpdir.name, "tmpl.txt")
        with open(template, "w", encoding="utf-8") as f:
            f.write("T")
        tree = os.path.join(self.tmpdir.name, "tree")
        os.mkdir(tree)
        f1 = os.path.join(tree, "file.txt")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("X")
        outdir = os.path.join(self.tmpdir.name, "out")
        os.mkdir(outdir)

        self.run_dispatch([
            template,
            "--tree-dirs",
            tree,
            "-o",
            outdir,
            "-j",
            "1",
            "--codex-bin",
            self.codex,
            "--passes",
            "2",
            "--map-name",
            "map.json",
        ])

        prefix = "1_" + os.path.basename(tree)
        out1 = os.path.join(outdir, f"1_{prefix}/file.txt-codex")
        out2 = os.path.join(outdir, f"2_{prefix}/file.txt-codex")
        with open(out1, "r", encoding="utf-8") as f:
            first = f.read()
        with open(out2, "r", encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, "T\nX")
        self.assertEqual(second, f"T\n{first}\nX")
        map_file = os.path.join(outdir, "map.json")
        with open(map_file, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping[0]["input"], os.path.abspath(f1))
        self.assertEqual(mapping[0]["output"], os.path.abspath(out1))


if __name__ == "__main__":
    unittest.main()
