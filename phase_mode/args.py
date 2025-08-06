"""Argument parser for phase mode dispatcher."""

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-template",
        dest="audit_template",
        default=None,
        help="path to the security-audit Codex template",
    )
    parser.add_argument(
        "--orchestrator-template",
        dest="orchestrator_template",
        required=True,
        help="path to the orchestrator chat-completion template",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--data-dir", dest="data_dir", help="directory containing input files")
    group.add_argument(
        "--tree-dirs",
        nargs="+",
        dest="tree_dirs",
        help="directories to recursively walk",
    )
    group.add_argument(
        "--file-list",
        dest="file_list",
        help="text file containing absolute/relative paths, one per line",
    )
    group.add_argument(
        "--findings-list",
        dest="findings_list",
        help="text file containing paths to pre-supplied finding outputs",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        required=True,
        help="directory for dispatcher outputs",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="recursively walk tree directories",
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
    parser.add_argument(
        "--max-inquiries",
        dest="max_inquiries",
        type=int,
        default=3,
        help="cap on orchestrator and Codex iterations per finding",
    )
    parser.add_argument(
        "--min-severity",
        dest="min_severity",
        choices=["low", "medium", "high", "critical"],
        default=None,
        help="minimum severity required for a vulnerability to be processed",
    )
    parser.add_argument(
        "--phase-root",
        dest="phase_root",
        default=None,
        help="top-level directory to resolve vulnerability file paths",
    )
    group_workers = parser.add_argument_group("parallelism")
    group_workers.add_argument(
        "--phase1-workers",
        type=int,
        default=os.cpu_count(),
        help="max workers for phase-1 ProcessPool (default: CPU count)",
    )
    group_workers.add_argument(
        "--phase2-workers",
        type=int,
        default=4,
        help="max workers for phase-2 ThreadPool & OpenAI semaphore (default: 4)",
    )
    return parser.parse_args()
