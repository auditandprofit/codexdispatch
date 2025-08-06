"""Dispatches Codex runs across multiple files and modes, coordinating workers and multi-pass processing.

TODO(#123): track per-file workdir enhancements.
See https://github.com/sourcegraph/codexdispatch/issues/123 for details.
"""

import os
import sys
import logging
import uuid
import subprocess
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from .args import parse_args
from .utils import collect_files, _invoke_codex, find_codex_bin, load_files
from .security_audit import run_security_audit


def _find_gitlab_findings(start: str) -> Optional[str]:
    """Return path to ``gitlab_findings.json`` searching upwards.

    Starting at ``start`` this walks up the directory tree until a
    ``gitlab_findings.json`` file is found.  If none is discovered the
    function returns ``None``.
    """

    cur = os.path.abspath(start)
    while True:
        cand = os.path.join(cur, "gitlab_findings.json")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _run_paramtrace_scan(path: str) -> None:
    """Scan Codex paramtrace outputs and resolve associated findings.

    If a ``findings.json`` file is present under ``path``, each Codex output
    file is matched against the ``files`` entries from the findings document.
    When a match is found, the finding object is attached to the result.
    """

    matches: list[dict[str, str]] = []

    findings_path = os.path.join(path, "findings.json")
    finding_lookup: dict[str, dict] = {}
    if os.path.exists(findings_path):
        try:
            with open(findings_path, "r", encoding="utf-8") as fh:
                findings = json.load(fh)
        except (OSError, json.JSONDecodeError):
            findings = {}
        for key, data in findings.items():
            slug = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:50]
            finding_lookup[slug] = data

    gitlab_findings_path = _find_gitlab_findings(path)
    gl_lookup: dict[str, str] = {}
    if gitlab_findings_path:
        try:
            with open(gitlab_findings_path, "r", encoding="utf-8") as fh:
                gitlab = json.load(fh)
        except (OSError, json.JSONDecodeError):
            gitlab = {}
        for key, data in gitlab.items():
            for f in data.get("files", []):
                gl_lookup[os.path.normpath(f)] = key

    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            fpath = os.path.join(dirpath, name)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
            except OSError:
                continue
            if text.startswith("```"):
                lines = text.splitlines()
                if lines:
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                text = "\n".join(lines)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            rel_fpath = os.path.relpath(fpath, path)
            parts = rel_fpath.split(os.sep)
            slug = parts[0] if parts else ""
            finding_obj = None
            candidate = finding_lookup.get(slug)
            if candidate:
                # Derive original file path from codex output name.
                source_path = os.path.join(*parts[1:]) if len(parts) > 1 else ""
                if source_path.endswith("-codex"):
                    source_path = source_path[: -len("-codex")]
                for f in candidate.get("files", []):
                    norm_candidate = os.path.normpath(f)
                    if norm_candidate.endswith(os.path.normpath(source_path)):
                        finding_obj = candidate.get("finding")
                        break

            # Derive original source path for GitLab lookup.
            src_path = rel_fpath.replace("gitlablib_paramtrace/", "", 1)
            if src_path.endswith("-codex"):
                src_path = src_path[: -len("-codex")]
            src_path = os.path.normpath(src_path)
            method = None
            for cand, key in gl_lookup.items():
                if cand.endswith(src_path):
                    method = key
                    break

            for chain, info in data.items():
                if isinstance(info, dict) and info.get("user_controlled") == "yes":
                    result = {
                        "file": fpath,
                        "param": chain,
                        "trace": info.get("trace", ""),
                        "evidence": info.get("evidence", ""),
                    }
                    if finding_obj:
                        result["finding"] = finding_obj
                    if method:
                        result["method"] = method
                    matches.append(result)

    print(json.dumps(matches, indent=2))


def _run_findings_mode(args) -> None:
    with open(args.template, "r", encoding="utf-8") as f:
        template = f.read()
    with open(args.findings_json, "r", encoding="utf-8") as jf:
        findings = json.load(jf)

    os.makedirs(args.output_dir, exist_ok=True)
    codex_bin = find_codex_bin(args.codex_bin)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    entries: list[tuple[str, dict, str]] = []
    all_paths: list[str] = []
    for key, data in findings.items():
        finding = data.get("finding", {})
        for path in data.get("files", []):
            entries.append((key, finding, path))
            all_paths.append(path)

    base: Optional[str] = None
    if args.relative_dir and all_paths:
        if len(all_paths) == 1:
            base = os.path.dirname(all_paths[0])
        else:
            base = os.path.commonpath(all_paths)

    def worker(item: tuple[str, dict, str]) -> None:
        key, finding, orig_path = item
        path = orig_path
        if args.relative_dir and base:
            rel = os.path.relpath(orig_path, base)
            path = os.path.join(args.relative_dir, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except UnicodeDecodeError:
            logging.warning("Skipping non-text file %s", path)
            return
        except OSError as exc:
            logging.error("Failed reading %s: %s", path, exc)
            return
        finding_json = json.dumps(finding, indent=2)
        full_path = os.path.abspath(path)
        prompt = f"{template}\n{finding_json}\n{full_path}\n{src}"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:50]
        rel_out = (
            os.path.relpath(path, args.relative_dir)
            if args.relative_dir
            else os.path.relpath(path)
        )
        out_path = os.path.join(args.output_dir, slug, f"{rel_out}-codex")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        work_dir = os.path.dirname(path)
        cmd = [
            codex_bin,
            "exec",
            "--output-last-message",
            out_path,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-C",
            work_dir,
        ]
        logging.info("FindingsMode: %s -> %s", path, out_path)
        try:
            _invoke_codex(cmd, prompt, args.timeout, path)
        except subprocess.TimeoutExpired as te:
            logging.error("TIMEOUT after %ss on %s", te.timeout, path)
        except subprocess.CalledProcessError as cpe:
            stderr = (cpe.stderr or b"").decode(errors="ignore")
            logging.error("Codex exit %s on %s\n%s", cpe.returncode, path, stderr)
        except Exception as exc:
            logging.error("Failed processing %s: %s", path, exc)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(worker, entries))


def main() -> None:
    args = parse_args()
    if getattr(args, "scan_paramtrace", None):
        _run_paramtrace_scan(args.scan_paramtrace)
        return
    if args.security_audit:
        run_security_audit(args)
        return
    if getattr(args, "findings_json", None):
        _run_findings_mode(args)
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

    root_prefix: Dict[str, str] = {}
    if args.tree_dirs:
        for idx, d in enumerate(args.tree_dirs, 1):
            base = os.path.basename(os.path.normpath(d))
            root_prefix[os.path.abspath(d)] = f"{idx}_{base}"

    file_list_entries: List[str] = []
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

    codex_bin = find_codex_bin(args.codex_bin)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    def rel_and_root(path: str):
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

    def build_prompt(data: str, prev_output: Optional[str]) -> str:
        if prev_output is None:
            return f"{template}\n{data}"
        return f"{template}\n{prev_output}\n{data}"

    def run_on_file(pass_idx: int, prev_outputs: Dict[str, str], path: str) -> None:
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
            except UnicodeDecodeError:
                prefix = "FileListMode:" if args.file_list else ""
                logging.warning("%sSkipping non-text file %s", prefix, path)
                return
            prev_output = prev_outputs.get(path)
            file_data = data
            if args.file_list and args.prepend_path:
                filename_line = os.path.abspath(path)
                file_data = f"{filename_line}\n{file_data}"
            prompt = build_prompt(file_data, prev_output)

            rel_path, root = rel_and_root(path)

            file_name = f"{pass_idx}_{rel_path}-codex" if passes > 1 else f"{rel_path}-codex"
            output_path = os.path.join(output_dir, file_name)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            if args.file_list:
                work_dir = (
                    os.path.dirname(path)
                    if args.per_file_workdir or args.work_dir is None
                    else args.work_dir
                )
            else:
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
        files = load_files(args.data_dir)
    else:
        files = [p for p in file_list_entries if os.path.exists(p)]
        missing = [p for p in file_list_entries if not os.path.exists(p)]
        for m in missing:
            logging.warning("FileListMode: missing path %s", m)

    for pass_idx in range(1, passes + 1):
        prev_cache: Dict[str, str] = {}
        if pass_idx > 1:
            for p in files:
                rel_path, _ = rel_and_root(p)
                prev_file = (
                    f"{pass_idx - 1}_{rel_path}-codex" if passes > 1 else f"{rel_path}-codex"
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
            mapping: List[dict[str, str]] = []
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
