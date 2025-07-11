import os
import sys
import subprocess
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor


"""Dispatch tool for running Codex on multiple input files in parallel."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", help="path to prompt template")
    parser.add_argument("data_dir", help="directory containing input files")
    parser.add_argument("output_dir", help="directory for codex outputs")
    parser.add_argument("workers", type=int, help="number of parallel workers")
    parser.add_argument(
        "-C",
        "--work-dir",
        default=os.getcwd(),
        help="working directory to run Codex in (default: current directory)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    template_path = args.template
    data_dir = args.data_dir
    output_dir = args.output_dir
    workers = args.workers

    with open(template_path, 'r') as f:
        template = f.read()

    os.makedirs(output_dir, exist_ok=True)

    codex_bin = os.path.join(os.path.dirname(__file__), 'codex-x86_64-unknown-linux-musl')
    if not os.path.exists(codex_bin):
        logging.error("Codex binary not found at %s", codex_bin)
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    def run_on_file(path: str) -> None:
        try:
            with open(path, "r") as f:
                data = f.read()
            prompt = template + "\n" + data
            output_path = os.path.join(output_dir, os.path.basename(path) + "-codex")
            cmd = [
                codex_bin,
                "exec",
                "--output-last-message",
                output_path,
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-C",
                args.work_dir,
            ]
            logging.info("Running codex on %s", path)
            subprocess.run(cmd, input=prompt.encode(), check=True)
            logging.info("Wrote %s", output_path)
        except Exception as exc:
            logging.error("Failed processing %s: %s", path, exc)

    files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if os.path.isfile(os.path.join(data_dir, f))]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(run_on_file, files))


if __name__ == '__main__':
    main()
