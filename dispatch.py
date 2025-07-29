import os
import sys
import subprocess
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor
import shutil


"""Dispatch tool for running Codex on multiple input files in parallel."""


def collect_files(dirs: list[str], recursive: bool = True) -> list[str]:
    files: list[str] = []
    for root in dirs:
        if recursive:
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    path = os.path.join(dirpath, name)
                    if os.path.isfile(path):
                        files.append(path)
        else:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    files.append(path)
    return sorted(files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", help="path to prompt template")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--data-dir",
        dest="data_dir",
        help="directory containing input files",
    )
    group.add_argument(
        "--tree-dirs",
        nargs="+",
        dest="tree_dirs",
        help="directories to recursively walk",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        required=True,
        help="directory for codex outputs",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        required=True,
        help="number of parallel workers",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="recursively walk tree directories",
    )
    parser.add_argument(
        "-C",
        "--work-dir",
        default=os.getcwd(),
        help="working directory to run Codex in (default: current directory)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("CODEX_DISPATCH_TIMEOUT", 900)),
        help="per-file timeout in seconds (0 = none, default 900)",
    )
    parser.add_argument(
        "--codex-bin",
        dest="codex_bin",
        default=None,
        help="path to codex binary. Defaults to searching PATH or current directory",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    template_path = args.template
    output_dir = args.output_dir
    workers = args.workers

    if args.data_dir and not args.recursive:
        logging.warning("--recursive option is ignored when using --data-dir")

    with open(template_path, "r") as f:
        template = f.read()

    os.makedirs(output_dir, exist_ok=True)

    root_prefix: dict[str, str] = {}
    if args.tree_dirs:
        for idx, d in enumerate(args.tree_dirs, 1):
            base = os.path.basename(os.path.normpath(d))
            root_prefix[os.path.abspath(d)] = f"{idx}_{base}"

    codex_bin = args.codex_bin
    if codex_bin:
        codex_bin = os.path.abspath(codex_bin)
        if not os.path.exists(codex_bin):
            logging.error("Codex binary not found at %s", codex_bin)
            sys.exit(1)
    else:
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            candidates = [
                f
                for f in os.listdir(os.getcwd())
                if os.path.isfile(f) and "codex" in f and os.access(f, os.X_OK)
            ]
            if len(candidates) == 1:
                codex_bin = os.path.abspath(candidates[0])
            elif len(candidates) > 1:
                logging.error(
                    "Multiple codex binaries found in current directory: %s",
                    ", ".join(candidates),
                )
                sys.exit(1)
            else:
                logging.error("Codex binary not found in PATH or current directory")
                sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    def run_on_file(path: str) -> None:
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
            except UnicodeDecodeError:
                logging.warning("Skipping non-text file %s", path)
                return
            prompt = template + "\n" + data

            if args.tree_dirs:
                root = next(
                    (
                        d
                        for d in args.tree_dirs
                        if os.path.commonpath(
                            [os.path.abspath(path), os.path.abspath(d)]
                        )
                        == os.path.abspath(d)
                    ),
                    os.path.dirname(path),
                )
                prefix = root_prefix.get(os.path.abspath(root), os.path.basename(root))
                rel_path = os.path.join(prefix, os.path.relpath(path, root))
            else:
                rel_path = os.path.relpath(path, args.data_dir)

            output_path = os.path.join(output_dir, rel_path + "-codex")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            work_dir = os.path.dirname(path) if args.tree_dirs else args.work_dir
            cmd = [
                codex_bin,
                "exec",
                "--output-last-message",
                output_path,
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-C",
                work_dir,
            ]
            if args.tree_dirs:
                logging.info(
                    "TreeMode: root=%s   file=%s   work_dir=%s", root, path, work_dir
                )
            else:
                logging.info("Running codex on %s", path)

            max_tries = 2
            for attempt in range(1, max_tries + 1):
                try:
                    subprocess.run(
                        cmd,
                        input=prompt.encode(),
                        check=True,
                        timeout=args.timeout or None,
                    )
                    break
                except subprocess.TimeoutExpired as te:
                    if attempt == max_tries:
                        raise
                    logging.warning("Retrying (%s/%s) %s", attempt, max_tries, path)

            logging.info("Wrote %s", output_path)
        except subprocess.TimeoutExpired as te:
            logging.error("TIMEOUT after %ss on %s", te.timeout, path)
        except subprocess.CalledProcessError as cpe:
            stderr = (cpe.stderr or b"").decode(errors="ignore")
            logging.error("Codex exit %s on %s\n%s", cpe.returncode, path, stderr)
        except Exception as exc:
            logging.error("Failed processing %s: %s", path, exc)

    if args.tree_dirs:
        files = collect_files(args.tree_dirs, recursive=args.recursive)
    else:
        data_dir = args.data_dir
        files = [
            os.path.join(dp, f)
            for dp, _, filenames in os.walk(data_dir)
            for f in filenames
            if os.path.isfile(os.path.join(dp, f))
        ]
        files = sorted(files)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(run_on_file, files))


if __name__ == "__main__":
    main()
