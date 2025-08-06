"""Utilities for copying gem methods based on parsed marker files."""

from __future__ import annotations

import os
from typing import List


def resolve_paths_from_parsed(focused_dir: str, parsed_dir: str) -> List[str]:
    """Return file paths under ``focused_dir`` corresponding to markers in ``parsed_dir``.

    The parsed directory may contain ``*.parsed.json`` marker files under arbitrary
    subdirectories. Each marker mirrors the path of a Ruby source file located under
    ``focused_dir``. This function translates those markers back to real source
    paths and only returns ones that actually exist.
    """
    resolved: List[str] = []
    for root, _, files in os.walk(parsed_dir):
        for name in files:
            if not name.endswith(".parsed.json"):
                continue
            marker_path = os.path.join(root, name)
            rel = os.path.relpath(marker_path, parsed_dir)
            parts = rel.split(os.sep)
            try:
                idx = parts.index("gem")
            except ValueError:
                continue
            rel_parts = parts[idx:]
            rel_parts[-1] = rel_parts[-1].removesuffix(".parsed.json")
            candidate = os.path.join(focused_dir, *rel_parts)
            if os.path.exists(candidate):
                resolved.append(candidate)
    return resolved

__all__ = ["resolve_paths_from_parsed"]
