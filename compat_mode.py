"""Backwards compatibility mode to correlate findings with vulnerabilities."""

import argparse
import json
import os
from pathlib import Path
from typing import Dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--findings-dir",
        dest="findings_dir",
        required=True,
        help="directory containing findings JSON files",
    )
    parser.add_argument(
        "--vuln-dir",
        dest="vuln_dir",
        required=True,
        help="directory containing original vulnerability JSON files",
    )
    parser.add_argument(
        "--phase-root",
        dest="phase_root",
        default=None,
        help="base directory to resolve file_path entries",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        required=True,
        help="directory for correlated outputs",
    )
    return parser.parse_args()


def _load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def run_compat_mode(args: argparse.Namespace) -> None:
    os.makedirs(args.output_dir, exist_ok=True)
    summary: Dict[str, Dict[str, dict]] = {}

    for name in sorted(os.listdir(args.findings_dir)):
        if not name.endswith(".json"):
            continue
        finding_path = os.path.join(args.findings_dir, name)
        finding_obj = _load_json(finding_path)
        if not finding_obj:
            continue
        vuln_id = finding_obj.get("id") or name.split("_", 1)[0]

        vuln_path = None
        for vname in os.listdir(args.vuln_dir):
            if vname.startswith(vuln_id):
                vuln_path = os.path.join(args.vuln_dir, vname)
                break
        if not vuln_path:
            continue
        vuln_obj = _load_json(vuln_path)
        if not vuln_obj:
            continue

        file_path = vuln_obj.get("file_path")
        source = ""
        resolved_path = file_path
        if file_path:
            candidates = []
            if os.path.isabs(file_path):
                candidates.append(file_path)
            if args.phase_root:
                candidates.append(os.path.join(args.phase_root, file_path))
                candidates.append(os.path.join(args.phase_root, "ee", file_path))
            for candidate in candidates:
                try:
                    with open(candidate, "r", encoding="utf-8") as sf:
                        source = sf.read()
                        resolved_path = candidate
                        break
                except OSError:
                    continue
        vuln_obj["file_path"] = resolved_path

        out_obj = {
            "summary": finding_obj,
            "vulnerability": vuln_obj,
            "source": source,
        }
        out_path = os.path.join(args.output_dir, name)
        with open(out_path, "w", encoding="utf-8") as ofh:
            json.dump(out_obj, ofh)

        summary[vuln_id] = {"summary": finding_obj, "vulnerability": vuln_obj}

    summary_path = os.path.join(args.output_dir, "findings_summary.json")
    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(summary, sf)


def main() -> None:
    args = parse_args()
    run_compat_mode(args)


if __name__ == "__main__":
    main()
