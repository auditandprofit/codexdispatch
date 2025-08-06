"""Utility to combine paramtrace and new finding traces."""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Set


def _load_paramtrace(path: str) -> Dict[str, Set[str]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    mapping: Dict[str, Set[str]] = {}
    for entry in data:
        method = entry.get("method")
        if not method:
            continue
        for trace in entry.get("trace", []):
            file_path = str(trace).split(":", 1)[0]
            mapping.setdefault(method, set()).add(file_path)
    return mapping


def _load_newfindings(path: str) -> Dict[str, Set[str]]:
    mapping: Dict[str, Set[str]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = entry.get("method")
            if not method:
                continue
            for trace in entry.get("trace", []):
                mapping.setdefault(method, set()).add(trace)
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paramtrace", required=True, help="path to paramtrace JSON file")
    parser.add_argument("--newfindings", required=True, help="path to new findings JSONL file")
    parser.add_argument("--output-dir", required=True, help="directory to write combined outputs")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    combined: Dict[str, Set[str]] = {}
    pt_map = _load_paramtrace(args.paramtrace)
    nf_map = _load_newfindings(args.newfindings)
    for method, paths in pt_map.items():
        combined.setdefault(method, set()).update(paths)
    for method, paths in nf_map.items():
        combined.setdefault(method, set()).update(paths)

    for method, paths in combined.items():
        out_name = method.replace(".", "_") + ".txt"
        out_path = os.path.join(args.output_dir, out_name)
        with open(out_path, "w", encoding="utf-8") as out:
            for p in sorted(paths):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        out.write(fh.read())
                except OSError:
                    continue


if __name__ == "__main__":
    main()
