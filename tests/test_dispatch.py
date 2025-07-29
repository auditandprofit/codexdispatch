import os
import sys
import subprocess
import tempfile
import json

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
