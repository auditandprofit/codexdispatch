#!/usr/bin/env python3
"""Utilities for copying Ruby gem methods.

This module exposes a mode that resolves Ruby source files based on
``*.parsed.json`` markers produced by a previous run. Each ``.parsed.json``
file is expected to mirror the path of a Ruby source file beneath a ``gem``
root inside a "focused gems" directory. The script resolves the corresponding
``.rb`` paths inside the provided focused gems directory and prints them one
per line.
"""

from __future__ import annotations

import argparse
import os
from typing import Iterable, List


SUFFIX = ".parsed.json"


def _strip_to_gem(path: str) -> str | None:
    """Return the relative path within ``gem/`` for *path*.

    The provided *path* must contain ``/gem/``. Any components before the first
    occurrence of ``gem/`` are removed. ``None`` is returned if ``gem/`` is not
    present.
    """

    normalised = path.replace("\\", "/")
    if "/gem/" not in normalised:
        return None
    return normalised.split("/gem/", 1)[1]


def resolve_paths_from_parsed(
    focused_gems_dir: str, parsed_dir: str
) -> List[str]:
    """Resolve Ruby source files from ``*.parsed.json`` markers.

    Parameters
    ----------
    focused_gems_dir:
        Directory containing a ``gem`` subdirectory with the Ruby sources from a
        previous run.
    parsed_dir:
        Directory tree containing ``*.parsed.json`` files whose paths mirror the
        desired Ruby sources under ``gem``.

    Returns
    -------
    List[str]
        Absolute paths to the Ruby ``.rb`` files inside ``focused_gems_dir``.
    """

    resolved: list[str] = []
    for root, _dirs, files in os.walk(parsed_dir):
        for name in files:
            if not name.endswith(SUFFIX):
                continue
            full = os.path.join(root, name)
            trimmed = full[: -len(SUFFIX)]
            rel = _strip_to_gem(trimmed)
            if rel is None:
                continue
            candidate = os.path.join(focused_gems_dir, "gem", rel)
            resolved.append(os.path.normpath(candidate))
    return resolved


def _mode_resolve(args: argparse.Namespace) -> None:
    paths = resolve_paths_from_parsed(args.focused_gems_dir, args.parsed_dir)
    for p in paths:
        print(p)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Copy gem methods based on parsed JSON markers"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_resolve = sub.add_parser(
        "resolve",
        help="Resolve Ruby paths from *.parsed.json files and print them",
    )
    p_resolve.add_argument(
        "--focused-gems-dir",
        required=True,
        help="Directory containing the focused gems (with a 'gem' subdir)",
    )
    p_resolve.add_argument(
        "--parsed-dir",
        required=True,
        help="Directory containing *.parsed.json files",
    )
    p_resolve.set_defaults(func=_mode_resolve)

    args = parser.parse_args(list(argv) if argv is not None else None)
    args.func(args)


if __name__ == "__main__":
    main()
