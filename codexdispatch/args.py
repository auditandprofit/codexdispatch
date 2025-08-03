"""Command-line argument parser for the Codex dispatcher and optional security audit mode."""

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", help="path to prompt template")
    parser.add_argument(
        "--security-audit",
        action="store_true",
        default=False,
        help="activate security auditor mode",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="max BFS layers for security audit",
    )
    parser.add_argument(
        "--audit-focus",
        dest="audit_focus",
        default=None,
        help="specific focus for the security audit",
    )
    parser.add_argument(
        "--audit-root",
        dest="audit_root",
        default=None,
        help=(
            "base directory for resolving leads and limiting the search "
            "during security audits"
        ),
    )
    parser.add_argument(
        "--mock-audit",
        action="store_true",
        default=False,
        help="generate fake audit responses instead of invoking Codex",
    )
    parser.add_argument(
        "--lead-score-ext",
        dest="lead_score_ext",
        default="",
        help="comma-separated extensions that increase priority",
    )
    parser.add_argument(
        "--lead-score-regex",
        dest="lead_score_regex",
        default=None,
        help="additional regex to prioritize file names",
    )
    parser.add_argument(
        "--lead-score-json",
        dest="lead_score_json",
        default=None,
        help="path to JSON with custom scoring rules",
    )
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
    group.add_argument(
        "--file-list",
        dest="file_list",
        help="text file containing absolute/relative paths, one per line",
    )
    group.add_argument(
        "--findings-json",
        dest="findings_json",
        help="GitLab findings.json input file",
    )
    parser.add_argument(
        "--prepend-path",
        dest="prepend_path",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prepend full resolved path before file contents in file-list mode",
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
        dest="work_dir",
        default=None,
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
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help="number of evaluation passes (tree mode only)",
    )
    parser.add_argument(
        "--map-name",
        dest="map_name",
        default=None,
        help="mapping filename for multi-pass mode",
    )
    parser.add_argument(
        "--relative-dir",
        dest="relative_dir",
        default=None,
        help="base directory to resolve findings file paths",
    )
    args = parser.parse_args()
    if args.mock_audit and not args.security_audit:
        parser.error("--mock-audit requires --security-audit")
    if args.security_audit:
        if not args.audit_focus:
            parser.error("--audit-focus is required with --security-audit")
        if args.depth < 1:
            parser.error("--depth must be >= 1")
        if args.passes != 1:
            parser.error("--passes is not supported with --security-audit")
    if args.findings_json and not args.work_dir:
        parser.error("--work-dir is required with --findings-json")
    if args.work_dir is None:
        args.work_dir = os.getcwd()
    return args
