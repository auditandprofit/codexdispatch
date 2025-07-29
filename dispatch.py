import os
import sys
import subprocess
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor
import shutil
import uuid
import json
from collections import defaultdict
import re
import random


"""Dispatch tool for running the Codex binary over many files in parallel.

Supports three modes:
```
--data-dir   : process every file in a directory
--tree-dirs  : recursively walk one or more directories
--file-list  : read explicit file paths from a text file
```

When using ``--file-list`` the working directory supplied with ``-C`` is used
for all Codex executions. Each prompt includes the full resolved path on a
separate line before the file contents.
"""


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


def _invoke_codex(cmd: list[str], prompt: str, timeout: int | None, path: str) -> None:
    max_tries = 2
    for attempt in range(1, max_tries + 1):
        try:
            subprocess.run(
                cmd,
                input=prompt.encode(),
                check=True,
                timeout=timeout or None,
            )
            break
        except subprocess.TimeoutExpired:
            if attempt == max_tries:
                raise
            logging.warning("Retrying (%s/%s) %s", attempt, max_tries, path)


def parse_codex_json(text: str) -> dict:
    try:
        return json.loads(text.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        logging.error("Non-JSON response")
        return {"notes": [], "followup": []}


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
    return args


AUDIT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "prompts", "security_audit_generic.txt"
)


def run_security_audit(args: argparse.Namespace) -> None:
    with open(AUDIT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        base_template = f.read()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    codex_bin = args.codex_bin
    if not args.mock_audit:
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
    else:
        codex_bin = codex_bin or "codex"

    os.makedirs(args.output_dir, exist_ok=True)

    root_prefix: dict[str, str] = {}
    if args.tree_dirs:
        for idx, d in enumerate(args.tree_dirs, 1):
            base = os.path.basename(os.path.normpath(d))
            root_prefix[os.path.abspath(d)] = f"{idx}_{base}"

    file_list_entries: list[str] = []
    if args.file_list:
        if not os.path.isdir(args.work_dir):
            logging.error("--work-dir %s does not exist", args.work_dir)
            sys.exit(1)
        with open(args.file_list, "r", encoding="utf-8") as f:
            file_list_entries = [
                p.strip()
                for p in f
                if p.strip() and not p.strip().startswith("#")
            ]
        file_list_entries = sorted(dict.fromkeys(file_list_entries))

    def expand_paths(paths: list[str]) -> tuple[list[str], list[str]]:
        out_files: list[str] = []
        roots: list[str] = []
        for raw in paths:
            p = os.path.abspath(raw)
            if os.path.isdir(p):
                roots.append(p)
                out_files.extend(collect_files([p], recursive=args.recursive))
            elif os.path.isfile(p):
                roots.append(os.path.dirname(p))
                out_files.append(p)
            else:
                logging.warning("FileListMode: missing path %s", raw)
        out_files = sorted(dict.fromkeys(out_files))
        roots = sorted(dict.fromkeys(roots))
        return out_files, roots

    if args.tree_dirs:
        files, root_dirs = expand_paths(args.tree_dirs)
    elif args.data_dir:
        data_dir = args.data_dir
        files = [
            os.path.join(dp, f)
            for dp, _, filenames in os.walk(data_dir)
            for f in filenames
            if os.path.isfile(os.path.join(dp, f))
        ]
        files = sorted(files)
        root_dirs = [os.path.abspath(data_dir)]
    else:
        files, root_dirs = expand_paths(file_list_entries)

    if args.audit_root:
        audit_root = os.path.abspath(args.audit_root)
        if not os.path.isdir(audit_root):
            logging.error("--audit-root %s does not exist", audit_root)
            sys.exit(1)
        root_dirs = [audit_root]

    all_files: list[str] = []
    if args.mock_audit:
        for rd in root_dirs:
            all_files.extend(collect_files([rd], recursive=True))
        all_files = sorted(dict.fromkeys(all_files))
        random.seed(0)

    def rel_and_root(path: str) -> tuple[str, str | None]:
        if args.tree_dirs:
            root = next(
                (
                    d
                    for d in args.tree_dirs
                    if os.path.commonpath([os.path.abspath(path), os.path.abspath(d)])
                    == os.path.abspath(d)
                ),
                os.path.dirname(path),
            )
            prefix = root_prefix.get(os.path.abspath(root), os.path.basename(root))
            rel_path = os.path.join(prefix, os.path.relpath(path, root))
            return rel_path, root
        elif args.data_dir:
            return os.path.relpath(path, args.data_dir), None
        else:
            return os.path.relpath(path), None

    def build_prompt(data: str, prev_blobs: list[str], depth: int) -> str:
        prefix = (
            base_template.replace("{depth}", str(depth)).replace("{goal}", args.audit_focus)
        )
        parts = [prefix]
        parts.extend(prev_blobs)
        parts.append(data)
        return "\n".join(parts)

    def resolve_path(lead: str) -> str | None:
        if args.audit_root:
            base = os.path.abspath(args.audit_root)
            cand = os.path.join(base, lead) if not os.path.isabs(lead) else lead
            cand = os.path.abspath(cand)
            if os.path.exists(cand) and os.path.commonpath([cand, base]) == base:
                return cand
            return None
        for rd in root_dirs:
            cand = os.path.join(rd, lead) if not os.path.isabs(lead) else lead
            cand = os.path.abspath(cand)
            if os.path.exists(cand) and os.path.commonpath([cand, rd]) == rd:
                return cand
        return None

    summary: dict[str, dict] = {}
    prev_map: defaultdict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()

    queue: list[tuple[str, str]] = [("path", os.path.abspath(p)) for p in files]
    depth_max = args.depth

    for depth in range(depth_max):
        if not queue:
            break
        next_q: list[tuple[str, str]] = []
        depth_dir = os.path.join(args.output_dir, "security", f"depth_{depth}")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}

            def worker(item: tuple[str, str]) -> tuple[str, list[tuple[str, str]]]:
                kind, token = item
                key = token
                if kind == "path":
                    try:
                        with open(token, "r", encoding="utf-8") as f:
                            data = f.read()
                    except UnicodeDecodeError:
                        logging.warning("Skipping non-text file %s", token)
                        return key, []
                    if args.file_list:
                        file_data = f"{os.path.abspath(token)}\n{data}"
                    else:
                        file_data = data
                else:
                    file_data = ""

                prompt = build_prompt(file_data, prev_map[key], depth)

                if kind == "path":
                    rel_path, root = rel_and_root(token)
                    out_base = os.path.join(depth_dir, f"{rel_path}-audit")
                    work_dir = args.work_dir if args.file_list else (
                        os.path.dirname(token) if args.tree_dirs else args.work_dir
                    )
                else:
                    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:50]
                    out_base = os.path.join(depth_dir, f"{slug}-audit")
                    work_dir = args.work_dir

                os.makedirs(os.path.dirname(out_base), exist_ok=True)

                if args.mock_audit:
                    notes = [f"mock note for {os.path.basename(token)}"]
                    follow = []
                    if all_files:
                        fp = random.choice(all_files)
                        root_dir = next(
                            (rd for rd in root_dirs if os.path.commonpath([fp, rd]) == rd),
                            root_dirs[0] if root_dirs else os.path.dirname(fp),
                        )
                        follow.append(os.path.relpath(fp, root_dir))
                    mock_res = {"notes": notes, "followup": follow}
                    with open(out_base, "w", encoding="utf-8") as mf:
                        mf.write("MOCK\n" + json.dumps(mock_res))
                else:
                    cmd = [
                        codex_bin,
                        "exec",
                        "--output-last-message",
                        out_base,
                        "--dangerously-bypass-approvals-and-sandbox",
                        "--skip-git-repo-check",
                        "-C",
                        work_dir,
                    ]

                    try:
                        _invoke_codex(cmd, prompt, args.timeout, key)
                    except subprocess.TimeoutExpired as te:
                        logging.error("TIMEOUT after %ss on %s", te.timeout, key)
                    except subprocess.CalledProcessError as cpe:
                        stderr = (cpe.stderr or b"").decode(errors="ignore")
                        logging.error("Codex exit %s on %s\n%s", cpe.returncode, key, stderr)
                    except Exception as exc:
                        logging.error("Failed processing %s: %s", key, exc)

                try:
                    with open(out_base, "r", encoding="utf-8") as of:
                        raw = of.read()
                except OSError:
                    raw = ""

                parsed = parse_codex_json(raw)
                with open(out_base + ".json", "w", encoding="utf-8") as jf:
                    json.dump(parsed, jf, indent=2)

                if raw:
                    prev_map[key].append(raw.splitlines()[-1])

                summary[key] = {"depth": depth, "notes": parsed.get("notes", [])}

                leads: list[tuple[str, str]] = []
                for fl in parsed.get("followup", []):
                    resolved = resolve_path(fl)
                    if resolved:
                        leads.append(("path", resolved))
                    else:
                        leads.append(("note", fl))
                return key, leads

            for item in queue:
                futures[executor.submit(worker, item)] = item

            results = [fut.result() for fut in futures]

        for key, leads in results:
            if depth < depth_max - 1:
                for ld in leads:
                    token = ld[1]
                    if token not in seen and ld not in next_q:
                        next_q.append(ld)

        seen.update([item[1] for item in queue])
        queue = [q for q in next_q if q[1] not in seen]

    with open(os.path.join(args.output_dir, "security_summary.json"), "w", encoding="utf-8") as sf:
        json.dump(summary, sf, indent=2)


def main() -> None:
    args = parse_args()

    if args.security_audit:
        run_security_audit(args)
        return

    template_path = args.template
    output_dir = args.output_dir
    workers = args.workers
    passes = max(1, args.passes)
    if passes > 1 and not args.tree_dirs:
        logging.error("--passes > 1 is only supported with --tree-dirs")
        sys.exit(1)
    map_name = args.map_name or f"mp_map_{uuid.uuid4().hex}.json"

    if args.data_dir and not args.recursive:
        logging.warning("--recursive option is ignored when using --data-dir")
    if args.file_list and not args.recursive:
        logging.warning("--recursive option is ignored when using --file-list")

    with open(template_path, "r") as f:
        template = f.read()

    os.makedirs(output_dir, exist_ok=True)

    root_prefix: dict[str, str] = {}
    if args.tree_dirs:
        for idx, d in enumerate(args.tree_dirs, 1):
            base = os.path.basename(os.path.normpath(d))
            root_prefix[os.path.abspath(d)] = f"{idx}_{base}"

    file_list_entries: list[str] = []
    if args.file_list:
        if not os.path.isdir(args.work_dir):
            logging.error("--work-dir %s does not exist", args.work_dir)
            sys.exit(1)
        with open(args.file_list, "r", encoding="utf-8") as f:
            file_list_entries = [
                p.strip()
                for p in f
                if p.strip() and not p.strip().startswith("#")
            ]
        file_list_entries = sorted(dict.fromkeys(file_list_entries))

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

    def rel_and_root(path: str) -> tuple[str, str | None]:
        if args.tree_dirs:
            root = next(
                (
                    d
                    for d in args.tree_dirs
                    if os.path.commonpath([os.path.abspath(path), os.path.abspath(d)])
                    == os.path.abspath(d)
                ),
                os.path.dirname(path),
            )
            prefix = root_prefix.get(os.path.abspath(root), os.path.basename(root))
            rel_path = os.path.join(prefix, os.path.relpath(path, root))
            return rel_path, root
        elif args.data_dir:
            return os.path.relpath(path, args.data_dir), None
        else:
            return os.path.relpath(path), None

    def build_prompt(data: str, prev_output: str | None) -> str:
        if prev_output is None:
            return f"{template}\n{data}"
        return f"{template}\n{prev_output}\n{data}"

    def run_on_file(pass_idx: int, prev_outputs: dict[str, str], path: str) -> None:
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
            except UnicodeDecodeError:
                prefix = "FileListMode:" if args.file_list else ""
                logging.warning("%sSkipping non-text file %s", prefix, path)
                return
            prev_output = prev_outputs.get(path)
            if args.file_list:
                filename_line = os.path.abspath(path)
                file_data = f"{filename_line}\n{data}"
            else:
                file_data = data
            prompt = build_prompt(file_data, prev_output)

            rel_path, root = rel_and_root(path)

            file_name = f"{pass_idx}_{rel_path}-codex" if passes > 1 else f"{rel_path}-codex"
            output_path = os.path.join(output_dir, file_name)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            work_dir = args.work_dir if args.file_list else (
                os.path.dirname(path) if args.tree_dirs else args.work_dir
            )
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
            if args.file_list:
                logging.info("FileListMode: %s -> %s", path, output_path)
            elif args.tree_dirs:
                logging.info(
                    "TreeMode: pass=%s root=%s   file=%s   work_dir=%s",
                    pass_idx,
                    root,
                    path,
                    work_dir,
                )
            else:
                logging.info("Running codex on %s", path)

            _invoke_codex(cmd, prompt, args.timeout, path)

            prefix = "FileListMode:" if args.file_list else ""
            logging.info("%sWrote %s", prefix, output_path)
        except subprocess.TimeoutExpired as te:
            logging.error("TIMEOUT after %ss on %s", te.timeout, path)
        except subprocess.CalledProcessError as cpe:
            stderr = (cpe.stderr or b"").decode(errors="ignore")
            logging.error("Codex exit %s on %s\n%s", cpe.returncode, path, stderr)
        except Exception as exc:
            logging.error("Failed processing %s: %s", path, exc)

    if args.tree_dirs:
        files = collect_files(args.tree_dirs, recursive=args.recursive)
    elif args.data_dir:
        data_dir = args.data_dir
        files = [
            os.path.join(dp, f)
            for dp, _, filenames in os.walk(data_dir)
            for f in filenames
            if os.path.isfile(os.path.join(dp, f))
        ]
        files = sorted(files)
    else:
        files = [p for p in file_list_entries if os.path.exists(p)]
        missing = [p for p in file_list_entries if not os.path.exists(p)]
        for m in missing:
            logging.warning("FileListMode: missing path %s", m)

    for pass_idx in range(1, passes + 1):
        prev_cache: dict[str, str] = {}
        if pass_idx > 1:
            for p in files:
                rel_path, _ = rel_and_root(p)
                prev_file = (
                    f"{pass_idx - 1}_{rel_path}-codex"
                    if passes > 1
                    else f"{rel_path}-codex"
                )
                prev_path = os.path.join(output_dir, prev_file)
                if os.path.exists(prev_path):
                    with open(prev_path, "r", encoding="utf-8") as pf:
                        prev_cache[p] = pf.read()
                else:
                    logging.error("Missing previous output %s", prev_path)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(lambda p: run_on_file(pass_idx, prev_cache, p), files))

        if pass_idx < passes:
            mapping: list[dict[str, str]] = []
            for p in files:
                rel_path, _ = rel_and_root(p)
                file_name = (
                    f"{pass_idx}_{rel_path}-codex" if passes > 1 else f"{rel_path}-codex"
                )
                out_path = os.path.join(output_dir, file_name)
                if os.path.exists(out_path):
                    mapping.append({"input": os.path.abspath(p), "output": os.path.abspath(out_path)})
            with open(os.path.join(output_dir, map_name), "w", encoding="utf-8") as mf:
                json.dump(mapping, mf, indent=2)


if __name__ == "__main__":
    main()
