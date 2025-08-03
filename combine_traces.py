#!/usr/bin/env python3
"""Combine paramtrace and new findings traces.

This script parses ``paramtrace_valid.json`` style files and
``newfindings.json`` style files, matches entries by ``method`` name, and
writes combined output files containing the JSON objects and the contents
of their traced source files.

Each output file contains::

    - The matching object from ``paramtrace_valid.json``
    - Contents of files referenced in its ``trace`` field
    - The matching object from ``newfindings.json``
    - Contents of files referenced in its ``trace`` field

Paramtrace and new findings trace paths are used as-is.  Line number
components (e.g. ``":L1-2"``) are stripped from the paramtrace paths but
no other path resolution is performed.  If a file cannot be read it is
noted in the output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Iterable, Dict, Any, List


def _load_paramtrace(path: str) -> List[Dict[str, Any]]:
    """Return parsed data from a ``paramtrace_valid.json`` file."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_newfindings(path: str) -> List[Dict[str, Any]]:
    """Return parsed data from a ``newfindings.json`` file."""
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def _sanitize_method(method: str) -> str:
    """Return a filesystem-friendly representation of *method*."""
    return re.sub(r"[^A-Za-z0-9]+", "_", method)


def _paramtrace_file_path(trace_entry: str) -> str:
    """Return normalized file path from a paramtrace trace entry."""
    file_part = trace_entry.split(":", 1)[0]
    return os.path.normpath(file_part)


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:  # pragma: no cover - error path
        return f"<unable to read {path!r}: {exc}>"


def _write_mapping(
    param_obj: Dict[str, Any],
    finding_obj: Dict[str, Any],
    out_dir: str,
) -> str:
    """Write the combined mapping to *out_dir* and return the file path."""
    os.makedirs(out_dir, exist_ok=True)
    name = _sanitize_method(param_obj["method"])
    out_path = os.path.join(out_dir, f"{name}.txt")
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("### Paramtrace Object\n")
        json.dump(param_obj, out, ensure_ascii=False, indent=2)
        out.write("\n\n### Paramtrace Sources\n")
        for rel in param_obj.get("trace", []):
            abs_path = _paramtrace_file_path(rel)
            out.write(f"--- {abs_path}\n")
            out.write(_read_file(abs_path))
            out.write("\n")
        out.write("\n### New Findings Object\n")
        json.dump(finding_obj, out, ensure_ascii=False, indent=2)
        out.write("\n\n### New Findings Sources\n")
        for path in finding_obj.get("trace", []):
            out.write(f"--- {path}\n")
            out.write(_read_file(path))
            out.write("\n")
    return out_path


def combine(
    paramtrace_path: str,
    newfindings_path: str,
    out_dir: str,
) -> List[str]:
    """Combine traces and return list of output files."""
    param_data = _load_paramtrace(paramtrace_path)
    new_data = _load_newfindings(newfindings_path)
    mapping = {item.get("method"): item for item in new_data}
    written: List[str] = []
    for obj in param_data:
        method = obj.get("method")
        if not method or method not in mapping:
            continue
        written.append(_write_mapping(obj, mapping[method], out_dir))
    return written


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Combine paramtrace and new findings traces",
    )
    parser.add_argument(
        "--paramtrace",
        default="paramtrace_valid.json",
        help="Path to paramtrace_valid.json data",
    )
    parser.add_argument(
        "--newfindings",
        default="newfindings.json",
        help="Path to newfindings.json data",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where combined files will be written",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    for path in combine(args.paramtrace, args.newfindings, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
