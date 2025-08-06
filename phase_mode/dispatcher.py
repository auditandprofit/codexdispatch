"""Dispatcher implementing phase mode only."""

import hashlib
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import Dict, List, Optional

import openai

from .args import parse_args
from .utils import collect_files, _invoke_codex, find_codex_bin, load_files
from . import openai_stub

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds



def _retry_openai(req_fn, *args, semaphore: threading.Semaphore | None = None, **kwargs):
    """Invoke `req_fn` with retries & back-off.

    Optionally gates concurrent calls using ``semaphore``.
    """
    fn_name = req_fn.__name__ if inspect.isfunction(req_fn) else repr(req_fn)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if semaphore is None:
                return req_fn(*args, **kwargs)
            with semaphore:
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


def _atomic_write_json(path: str, data: dict) -> None:
    """Atomically write ``data`` as JSON to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(target.parent)) as tf:
        json.dump(data, tf)
        tmp_name = tf.name
    os.replace(tmp_name, target)


def _split_phase1_findings(phase1_dir: str) -> None:
    """Split consolidated findings into one file per vulnerability."""
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


def call_orchestrator(
    prompt: str,
    env: dict[str, str] | None = None,
    semaphore: threading.Semaphore | None = None,
) -> dict:
    """Return {"inquiry": "..."} or {"conclusion": "valid|invalid", "summary": "..."}."""
    if env:
        old_env = os.environ.copy()
        os.environ.update(env)
    else:
        old_env = None
    try:
        schema = [
            {
                "name": "orchestrator_decision",
                "description": "Ask for more information or conclude the audit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "inquiry": {"type": "string"},
                        "conclusion": {"type": "string", "enum": ["valid", "invalid"]},
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
                    "You are a security audit orchestrator. Always respond"
                    " using the orchestrator_decision function."
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
                semaphore=semaphore,
            )
        except Exception:
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
        text = getattr(response, "output_text", "")
        if not text:
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for part in getattr(item, "content", []) or []:
                        txt = getattr(part, "text", None)
                        if txt:
                            text += txt
        if text:
            try:
                raw = json.loads(text.strip())
            except json.JSONDecodeError:
                raw = {}
            if "inquiry" in raw:
                return {"inquiry": raw["inquiry"]}
            if "conclusion" in raw:
                return {
                    "conclusion": raw.get("conclusion"),
                    "summary": raw.get("summary", ""),
                }
    finally:
        if old_env is not None:
            os.environ.clear()
            os.environ.update(old_env)
    return {}


def process_file(path: str, args) -> None:
    """Process a single source file for phase 1."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except UnicodeDecodeError:
        logging.warning("PhaseMode: skipping non-text file %s", path)
        return
    except OSError as exc:
        logging.error("PhaseMode: failed reading %s: %s", path, exc)
        return
    full_path = os.path.abspath(path)
    prompt = f"{args.audit_template_text}\n{full_path}\n{data}"
    rel_path = os.path.splitdrive(full_path)[1].lstrip(os.sep)
    if not rel_path.endswith("-codex"):
        rel_path = f"{rel_path}-codex"
    out_path = os.path.join(args.phase1_dir, rel_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        args.codex_bin,
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


def process_finding(name: str, args, orchestrator_env: dict[str, str] | None, semaphore: threading.Semaphore) -> dict:
    """Process a single finding JSON file and return verdict."""
    phase1_dir = args.phase1_dir
    final_dir = args.final_dir
    cache_dir = args.cache_dir

    finding_path = os.path.join(phase1_dir, name)
    try:
        with open(finding_path, "r", encoding="utf-8") as fh:
            finding_obj = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("PhaseMode: failed reading %s: %s", finding_path, exc)
        return {}

    if args.min_severity:
        sev = finding_obj.get("severity")
        ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if sev is None or ranks.get(str(sev).lower(), -1) < ranks[args.min_severity]:
            return {}

    vuln_id = str(finding_obj.get("id"))
    file_path = finding_obj.get("file_path")
    if not file_path:
        logging.warning("PhaseMode: skipping vuln %s without file_path", vuln_id)
        return {}
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
        return {}
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
        prompt = f"{args.orchestrator_template_text}\n{finding_json}\n{source}\n{prior_ctx}"
        try:
            result = call_orchestrator(prompt, env=orchestrator_env, semaphore=semaphore)
        except TypeError:
            result = call_orchestrator(prompt, env=orchestrator_env)
        if "conclusion" in result:
            depth = len(context) + 1
            final_path = os.path.join(final_dir, name)
            _atomic_write_json(final_path, result)
            verdict = {
                "file_path": file_path,
                "conclusion": result.get("conclusion"),
                "summary": result.get("summary", ""),
                "severity": severity,
                "depth": depth,
            }
            _atomic_write_json(cache_path, {"context": context, "status": "concluded"})
            return {vuln_id: verdict}
        inquiry = result.get("inquiry")
        if not inquiry:
            break
        phase = idx + 2
        out_path = os.path.join(args.output_dir, f"phase_{phase}", name)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            args.codex_bin,
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
            logging.error("Codex exit %s on inquiry %s\n%s", cpe.returncode, finding_path, stderr)
            break
        except Exception as exc:
            logging.error("Failed inquiry %s: %s", finding_path, exc)
            break
        try:
            with open(out_path, "r", encoding="utf-8") as rf:
                response = rf.read()
        except OSError:
            response = ""
        _atomic_write_json(out_path + ".meta", {"inquiry": inquiry, "response": response})
        context.append({"inquiry": inquiry, "response": response})
        _atomic_write_json(cache_path, {"context": context, "status": "open"})

    depth = len(context)
    final_path = os.path.join(final_dir, name)
    summary = "No conclusion: inquiry budget exhausted"
    _atomic_write_json(final_path, {"conclusion": "inconclusive", "summary": summary})
    verdict = {
        "file_path": file_path,
        "conclusion": "inconclusive",
        "summary": summary,
        "severity": severity,
        "depth": depth,
    }
    _atomic_write_json(cache_path, {"context": context, "status": "concluded"})
    return {vuln_id: verdict}


def run_phase_mode(args, orchestrator_env: dict[str, str] | None = None) -> None:
    try:
        with open(args.orchestrator_template, "r", encoding="utf-8") as f:
            orchestrator_template = f.read()
        args.orchestrator_template_text = orchestrator_template
        if not args.findings_list:
            with open(args.audit_template, "r", encoding="utf-8") as f:
                args.audit_template_text = f.read()
    except OSError as exc:
        logging.error("PhaseMode: failed reading template: %s", exc)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args.codex_bin = find_codex_bin(args.codex_bin)

    phase1_dir = os.path.join(args.output_dir, "phase_1")
    final_dir = os.path.join(args.output_dir, "final")
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(args.output_dir)), "cache")
    args.phase1_dir = phase1_dir
    args.final_dir = final_dir
    args.cache_dir = cache_dir
    Path(phase1_dir).mkdir(parents=True, exist_ok=True)
    Path(final_dir).mkdir(parents=True, exist_ok=True)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
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

        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            list(pool.map(process_file, inputs, repeat(args)))

    _split_phase1_findings(phase1_dir)

    finding_names = sorted(f for f in os.listdir(phase1_dir) if f.endswith(".json"))
    openai_sem = threading.Semaphore(4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                process_finding,
                finding_names,
                repeat(args),
                repeat(orchestrator_env),
                repeat(openai_sem),
            )
        )
    verdicts: dict[str, dict] = {}
    for res in results:
        verdicts.update(res)

    if verdicts:
        Path(final_dir).mkdir(parents=True, exist_ok=True)
        verdict_path = os.path.join(final_dir, "verdicts.json")
        _atomic_write_json(verdict_path, verdicts)



def main() -> None:
    args = parse_args()
    run_phase_mode(args)
