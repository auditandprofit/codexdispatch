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
import hashlib
import shutil
import inspect
import time
import openai
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from .args import parse_args
from .utils import collect_files, _invoke_codex, find_codex_bin, load_files
from .security_audit import run_security_audit


MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds


def _retry_openai(req_fn, *args, **kwargs):
    """Invoke `req_fn` with retries & back-off."""
    fn_name = req_fn.__name__ if inspect.isfunction(req_fn) else repr(req_fn)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return req_fn(*args, **kwargs)
        except (openai.OpenAIError, TypeError, RuntimeError) as err:
            logging.error(
                "OpenAI error in %s (try %d/%d): %s",
                fn_name,
                attempt,
                MAX_RETRIES,
                err,
            )
            if attempt == MAX_RETRIES:
                logging.critical("Retries exhausted; aborting.")
                print(f"FATAL: {err}", file=sys.stderr, flush=True)
                sys.exit(1)
            time.sleep(BACKOFF_BASE ** attempt)


def _strip_backticks(text: str) -> str:
    """Remove surrounding markdown code fences from ``text`` if present."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text


def _split_phase1_findings(phase1_dir: str) -> None:
    """Split consolidated findings into one file per vulnerability.

    ``phase_1`` outputs may contain a ``vulnerabilities`` array with multiple
    vulnerability objects.  Each object is written to ``phase_1`` as
    ``<id>_<orig-file>.json`` where ``orig-file`` is derived from the
    vulnerability's ``file_path`` (falling back to the source filename).
    The original findings files are removed after splitting.
    """

    to_delete: list[str] = []
    for dirpath, _, filenames in os.walk(phase1_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            vulns = []
            if isinstance(data, list):
                vulns = data
            elif isinstance(data, dict):
                for key in ("vulnerabilities", "findings"):
                    if isinstance(data.get(key), list):
                        vulns = data[key]
                        break
                else:
                    continue
            else:
                continue
            for vuln in vulns:
                vid = str(vuln.get("id")) if isinstance(vuln, dict) else None
                if not vid:
                    continue
                orig = os.path.basename(vuln.get("file_path") or name)
                out_name = f"{vid}_{orig}.json"
                out_path = os.path.join(phase1_dir, out_name)
                try:
                    with open(out_path, "w", encoding="utf-8") as ofh:
                        json.dump(vuln, ofh)
                except OSError:
                    continue
            to_delete.append(path)
    for path in to_delete:
        try:
            os.remove(path)
        except OSError:
            pass
    for dirpath, dirnames, filenames in os.walk(phase1_dir, topdown=False):
        if dirpath == phase1_dir:
            continue
        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def call_orchestrator(prompt: str, env: dict[str, str] | None = None) -> dict:
    """Return {"inquiry": "..."} or {"conclusion": "valid|invalid", "summary": "..."}."""
    if env:
        old_env = os.environ.copy()
        os.environ.update(env)
    else:
        old_env = None
    try:
        from . import openai_stub

        schema = [
            {
                "name": "orchestrator_decision",
                "description": "Ask for more information or conclude the audit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "inquiry": {"type": "string"},
                        "conclusion": {
                            "type": "string",
                            "enum": ["valid", "invalid"],
                        },
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a security audit orchestrator. Use the"
                    " orchestrator_decision function to either request"
                    " further details or conclude whether the finding is"
                    " valid or invalid. When concluding, provide a concise"
                    " reasoning summary in the 'summary' field."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            response = _retry_openai(
                openai_stub.openai_generate_response,
                messages=messages,
                functions=schema,
                model="o3",
                reasoning_effort="high",
                service_tier="flex",
            )
        except Exception as err:
            # Propagate to outer retry logic; no stub fall-back
            raise
        name, data = openai_stub.openai_parse_function_call(response)
        if name == "orchestrator_decision" and isinstance(data, dict):
            if "inquiry" in data:
                return {"inquiry": data["inquiry"]}
            if "conclusion" in data:
                return {
                    "conclusion": data["conclusion"],
                    "summary": data.get("summary", ""),
                }
    finally:
        if old_env is not None:
            for k in env or {}:
                if k in old_env:
                    os.environ[k] = old_env[k]
                else:
                    os.environ.pop(k, None)
    raise RuntimeError("Orchestrator did not return a decision")


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


def _run_phase_mode(args, orchestrator_env: dict[str, str] | None = None) -> None:
    try:
        with open(args.orchestrator_template, "r", encoding="utf-8") as f:
            orchestrator_template = f.read()
        if not args.findings_list:
            with open(args.audit_template, "r", encoding="utf-8") as f:
                audit_template = f.read()
    except OSError as exc:
        logging.error("PhaseMode: failed reading template: %s", exc)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    codex_bin = find_codex_bin(args.codex_bin)

    phase1_dir = os.path.join(args.output_dir, "phase_1")
    final_dir = os.path.join(args.output_dir, "final")
    os.makedirs(phase1_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(args.output_dir)), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    if args.findings_list:
        try:
            with open(args.findings_list, "r", encoding="utf-8") as fh:
                raw = [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]
        except OSError as exc:
            logging.error("PhaseMode: failed reading findings list: %s", exc)
            return
        seen: set[str] = set()
        inputs: List[str] = []
        for p in raw:
            if p not in seen:
                seen.add(p)
                inputs.append(p)
        for src in inputs:
            if not os.path.isfile(src):
                logging.warning("PhaseMode: skipping missing finding %s", src)
                continue
            dest = os.path.join(phase1_dir, os.path.basename(src))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.copyfile(src, dest)
            except OSError as exc:
                logging.error("PhaseMode: failed copying %s: %s", src, exc)
                continue
    else:
        if args.tree_dirs:
            inputs = collect_files(args.tree_dirs, recursive=args.recursive)
        elif args.data_dir:
            inputs = load_files(args.data_dir)
        elif args.file_list:
            with open(args.file_list, "r", encoding="utf-8") as fh:
                inputs = [p.strip() for p in fh if p.strip() and not p.strip().startswith("#")]
        else:
            inputs = []
        inputs = sorted(dict.fromkeys(inputs))

        # Phase 1 – run security audit
        for path in inputs:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
            except UnicodeDecodeError:
                logging.warning("PhaseMode: skipping non-text file %s", path)
                continue
            except OSError as exc:
                logging.error("PhaseMode: failed reading %s: %s", path, exc)
                continue
            full_path = os.path.abspath(path)
            prompt = f"{audit_template}\n{full_path}\n{data}"
            rel_path = os.path.splitdrive(full_path)[1].lstrip(os.sep)
            if not rel_path.endswith("-codex"):
                rel_path = f"{rel_path}-codex"
            out_path = os.path.join(phase1_dir, rel_path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            cmd = [
                codex_bin,
                "exec",
                "--output-last-message",
                out_path,
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-C",
                os.path.dirname(path),
            ]
            logging.info("PhaseMode: phase_1 file=%s -> %s", path, out_path)
            try:
                _invoke_codex(cmd, prompt, args.timeout, path)
            except subprocess.TimeoutExpired as te:
                logging.error("TIMEOUT after %ss on %s", te.timeout, path)
            except subprocess.CalledProcessError as cpe:
                stderr = (cpe.stderr or b"").decode(errors="ignore")
                logging.error("Codex exit %s on %s\n%s", cpe.returncode, path, stderr)
            except Exception as exc:
                logging.error("Failed processing %s: %s", path, exc)

    # Split phase_1 outputs into per-vulnerability files
    _split_phase1_findings(phase1_dir)

    verdicts: dict[str, dict] = {}

    # Phase 2..N – orchestrator loop
    for name in sorted(f for f in os.listdir(phase1_dir) if f.endswith(".json")):
        finding_path = os.path.join(phase1_dir, name)
        try:
            with open(finding_path, "r", encoding="utf-8") as fh:
                finding_obj = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logging.error("PhaseMode: failed reading %s: %s", finding_path, exc)
            continue
        if args.min_severity:
            sev = finding_obj.get("severity")
            ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            if sev is None or ranks.get(str(sev).lower(), -1) < ranks[args.min_severity]:
                continue

        vuln_id = str(finding_obj.get("id"))
        file_path = finding_obj.get("file_path")
        if not file_path:
            logging.warning("PhaseMode: skipping vuln %s without file_path", vuln_id)
            continue
        severity = finding_obj.get("severity")
        cache_key_src = f"{vuln_id}:{file_path}"
        cache_key = hashlib.sha256(cache_key_src.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, f"{cache_key}.json")
        cache_entry = {"context": [], "status": "open"}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as cf:
                    cache_entry = json.load(cf)
            except (OSError, json.JSONDecodeError):
                pass
        if cache_entry.get("status") == "concluded":
            continue
        context = cache_entry.get("context", [])
        source = ""
        resolved_path = file_path
        work_dir = os.path.dirname(os.path.abspath(resolved_path))
        try:
            with open(resolved_path, "r", encoding="utf-8") as sf:
                source = sf.read()
        except OSError:
            if args.phase_root:
                alt_path = os.path.join(args.phase_root, file_path)
                try:
                    with open(alt_path, "r", encoding="utf-8") as sf:
                        source = sf.read()
                        resolved_path = alt_path
                        work_dir = os.path.dirname(os.path.abspath(resolved_path))
                except OSError:
                    ee_alt_path = os.path.join(args.phase_root, "ee", file_path)
                    try:
                        with open(ee_alt_path, "r", encoding="utf-8") as sf:
                            source = sf.read()
                            resolved_path = ee_alt_path
                            work_dir = os.path.dirname(os.path.abspath(resolved_path))
                    except OSError:
                        logging.warning("PhaseMode: unable to read %s", file_path)
            else:
                logging.warning("PhaseMode: unable to read %s", file_path)

        finding_json = json.dumps(finding_obj, indent=2)
        concluded = False
        for idx in range(len(context), args.max_inquiries):
            prior_ctx = json.dumps(context, indent=2)
            prompt = f"{orchestrator_template}\n{finding_json}\n{source}\n{prior_ctx}"
            result = call_orchestrator(prompt, env=orchestrator_env)
            if "conclusion" in result:
                depth = len(context) + 1
                final_path = os.path.join(final_dir, name)
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                with open(final_path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh)
                verdicts[vuln_id] = {
                    "file_path": file_path,
                    "conclusion": result.get("conclusion"),
                    "severity": severity,
                    "depth": depth,
                }
                cache_entry = {"context": context, "status": "concluded"}
                with open(cache_path, "w", encoding="utf-8") as cf:
                    json.dump(cache_entry, cf)
                concluded = True
                break
            inquiry = result.get("inquiry")
            if not inquiry:
                break
            phase = idx + 2
            out_path = os.path.join(args.output_dir, f"phase_{phase}", name)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
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
            try:
                _invoke_codex(cmd, f"{finding_json}\n{inquiry}", args.timeout, finding_path)
            except subprocess.TimeoutExpired as te:
                logging.error("TIMEOUT after %ss on inquiry %s", te.timeout, finding_path)
                break
            except subprocess.CalledProcessError as cpe:
                stderr = (cpe.stderr or b"").decode(errors="ignore")
                logging.error(
                    "Codex exit %s on inquiry %s\n%s", cpe.returncode, finding_path, stderr
                )
                break
            except Exception as exc:
                logging.error("Failed inquiry %s: %s", finding_path, exc)
                break
            try:
                with open(out_path, "r", encoding="utf-8") as rf:
                    response = rf.read()
            except OSError:
                response = ""
            with open(out_path + ".meta", "w", encoding="utf-8") as mf:
                json.dump({"inquiry": inquiry, "response": response}, mf)
            context.append({"inquiry": inquiry, "response": response})
            cache_entry = {"context": context, "status": "open"}
            with open(cache_path, "w", encoding="utf-8") as cf:
                json.dump(cache_entry, cf)
        if not concluded and cache_entry.get("status") != "concluded":
            depth = len(context)
            final_path = os.path.join(final_dir, name)
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            with open(final_path, "w", encoding="utf-8") as fh:
                json.dump({"conclusion": "inconclusive"}, fh)
            verdicts[vuln_id] = {
                "file_path": file_path,
                "conclusion": "inconclusive",
                "severity": severity,
                "depth": depth,
            }
            cache_entry = {"context": context, "status": "concluded"}
            with open(cache_path, "w", encoding="utf-8") as cf:
                json.dump(cache_entry, cf)

    if verdicts:
        os.makedirs(final_dir, exist_ok=True)
        verdict_path = os.path.join(final_dir, "verdicts.json")
        with open(verdict_path, "w", encoding="utf-8") as vf:
            json.dump(verdicts, vf)


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
    if getattr(args, "phase_mode", False):
        _run_phase_mode(args)
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
