"""Command-line argument parser for the Codex dispatcher and optional security audit mode."""

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", nargs="?", help="path to prompt template")
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
    group = parser.add_mutually_exclusive_group()
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
    group.add_argument(
        "--findings-list",
        dest="findings_list",
        help="text file containing paths to pre-supplied finding outputs",
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
        required=False,
        help="directory for codex outputs",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        required=False,
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
        "--per-file-workdir",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="in --file-list mode, set -C to the parent directory of each file",
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
    parser.add_argument(
        "--scan-paramtrace",
        dest="scan_paramtrace",
        default=None,
        help="recursively scan directory for paramtrace outputs",
    )
    parser.add_argument(
        "--phase-mode",
        action="store_true",
        default=False,
        help="enable multi-phase processing using audit and orchestrator templates",
    )
    parser.add_argument(
        "--audit-template",
        dest="audit_template",
        default=None,
        help="path to the security-audit Codex template",
    )
    parser.add_argument(
        "--orchestrator-template",
        dest="orchestrator_template",
        default=None,
        help="path to the orchestrator chat-completion template",
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
        help="minimum severity required for a vulnerability to be processed by the orchestrator in phase mode",
    )
    args = parser.parse_args()
    if args.scan_paramtrace:
        return args
    if args.phase_mode:
        if args.findings_list:
            if any(
                [
                    args.data_dir,
                    args.tree_dirs,
                    args.file_list,
                    args.findings_json,
                    args.audit_template,
                ]
            ):
                parser.error(
                    "--findings-list is incompatible with --data-dir, --tree-dirs, --file-list, --findings-json, and --audit-template"
                )
            if args.orchestrator_template is None:
                parser.error("--orchestrator-template is required with --findings-list")
            if args.output_dir is None or args.workers is None:
                parser.error("--output-dir and --workers are required")
            return args
        if not args.audit_template or not args.orchestrator_template:
            parser.error("--audit-template and --orchestrator-template are required with --phase-mode")
        if not any([args.data_dir, args.tree_dirs, args.file_list, args.findings_json]):
            parser.error(
                "one of --data-dir, --tree-dirs, --file-list, or --findings-json is required"
            )
        if args.output_dir is None or args.workers is None:
            parser.error("--output-dir and --workers are required")
        return args
    if args.min_severity:
        parser.error("--min-severity requires --phase-mode")
    if args.findings_list:
        parser.error("--findings-list requires --phase-mode")
    if args.template is None:
        parser.error("template is required")
    if not any([args.data_dir, args.tree_dirs, args.file_list, args.findings_json]):
        parser.error(
            "one of --data-dir, --tree-dirs, --file-list, or --findings-json is required"
        )
    if args.output_dir is None or args.workers is None:
        parser.error("--output-dir and --workers are required")
    if args.mock_audit and not args.security_audit:
        parser.error("--mock-audit requires --security-audit")
    if args.security_audit:
        if not args.audit_focus:
            parser.error("--audit-focus is required with --security-audit")
        if args.depth < 1:
            parser.error("--depth must be >= 1")
        if args.passes != 1:
            parser.error("--passes is not supported with --security-audit")
    if args.findings_json:
        if args.work_dir:
            parser.error("--work-dir is not allowed with --findings-json")
    else:
        if args.work_dir is None:
            args.work_dir = os.getcwd()
    return args
