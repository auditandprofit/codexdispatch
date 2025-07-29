import os
import sys
import logging
import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from heapq import heappush, heappop
from pathlib import Path
import re

from .utils import collect_files, parse_codex_json, _invoke_codex, find_codex_bin

AUDIT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "security_audit_generic.txt"
)


def run_security_audit(args) -> None:
    with open(AUDIT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        base_template = f.read()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.mock_audit:
        codex_bin = find_codex_bin(args.codex_bin)
    else:
        codex_bin = args.codex_bin or "codex"

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

    RISK_EXT = {".pem", ".key", ".env", ".crt", ".p12", ".jfrog", ".kube"}
    if getattr(args, "lead_score_ext", None):
        for ext in args.lead_score_ext.split(","):
            ext = ext.strip()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            RISK_EXT.add(ext)

    sensitive_pattern = r"(?i)(password|secret|credential|token|private|aws|gcp|azure)"
    if getattr(args, "lead_score_regex", None):
        sensitive_pattern = f"{sensitive_pattern}|({args.lead_score_regex})"
    SENSITIVE_RE = re.compile(sensitive_pattern)

    extra_rules: list[tuple[re.Pattern, int]] = []
    if getattr(args, "lead_score_json", None):
        try:
            with open(args.lead_score_json, "r", encoding="utf-8") as jf:
                data = json.load(jf)
            for pat, weight in data.items():
                try:
                    extra_rules.append((re.compile(pat, re.I), int(weight)))
                except re.error as rex:
                    logging.error("Invalid regex %s in %s: %s", pat, args.lead_score_json, rex)
        except OSError as exc:
            logging.error("Failed reading %s: %s", args.lead_score_json, exc)

    def score(path: str) -> int:
        name = os.path.basename(path)
        priority = -10 if Path(path).suffix in RISK_EXT else 0
        if SENSITIVE_RE.search(name):
            priority -= 8
        for rgx, weight in extra_rules:
            if rgx.search(name):
                priority += weight
        return priority

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

    fifo = 0
    queue: list[tuple[tuple[int, int, int, int], tuple[str, str]]] = []
    for p in files:
        heappush(
            queue,
            (
                (score(p), p.count(os.sep), len(os.path.basename(p)), fifo),
                ("path", os.path.abspath(p)),
            ),
        )
        fifo += 1

    depth_max = args.depth

    for depth in range(depth_max):
        if not queue:
            break
        current_items: list[tuple[str, str]] = []
        while queue:
            _, item = heappop(queue)
            current_items.append(item)

        next_q: list[tuple[tuple[int, int, int, int], tuple[str, str]]] = []
        queued_tokens: set[str] = set()
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

            for item in current_items:
                futures[executor.submit(worker, item)] = item

            results = [fut.result() for fut in futures]

        for key, leads in results:
            if depth < depth_max - 1:
                for ld in leads:
                    token = ld[1]
                    if token not in seen and token not in queued_tokens:
                        prio = score(token) if ld[0] == "path" else 0
                        heappush(
                            next_q,
                            (
                                (prio, token.count(os.sep), len(os.path.basename(token)), fifo),
                                ld,
                            ),
                        )
                        queued_tokens.add(token)
                        fifo += 1

        seen.update([item[1] for item in current_items])
        filtered: list[tuple[tuple[int, int, int, int], tuple[str, str]]] = []
        while next_q:
            pr, it = heappop(next_q)
            if it[1] not in seen:
                heappush(filtered, (pr, it))
        queue = filtered

    with open(os.path.join(args.output_dir, "security_summary.json"), "w", encoding="utf-8") as sf:
        json.dump(summary, sf, indent=2)
