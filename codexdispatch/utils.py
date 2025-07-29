import os
import sys
import subprocess
import logging
import json
import shutil


def load_files(data_dir: str) -> list[str]:
    """Return a sorted list of file paths under *data_dir*.

    The directory is walked recursively and only regular files are returned.
    """

    return sorted(
        [
            os.path.join(dp, f)
            for dp, _, filenames in os.walk(data_dir)
            for f in filenames
            if os.path.isfile(os.path.join(dp, f))
        ]
    )


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


def find_codex_bin(path_hint: str | None) -> str:
    """Return the path to the codex binary.

    If *path_hint* is provided it must exist, otherwise the function searches
    ``PATH`` and finally the current directory for executables containing
    ``"codex"`` in their name. The process exits with status 1 if no suitable
    binary is found or if multiple candidates exist in the current directory.
    """

    if path_hint:
        codex_path = os.path.abspath(path_hint)
        if not os.path.exists(codex_path):
            logging.error("Codex binary not found at %s", codex_path)
            sys.exit(1)
        return codex_path

    codex_bin = shutil.which("codex")
    if codex_bin is not None:
        return codex_bin

    candidates = [
        f
        for f in os.listdir(os.getcwd())
        if os.path.isfile(f) and "codex" in f and os.access(f, os.X_OK)
    ]
    if len(candidates) == 1:
        return os.path.abspath(candidates[0])
    if len(candidates) > 1:
        logging.error(
            "Multiple codex binaries found in current directory: %s",
            ", ".join(candidates),
        )
        sys.exit(1)

    logging.error("Codex binary not found in PATH or current directory")
    sys.exit(1)


def _invoke_codex(cmd: list[str], prompt: str, timeout: int | None, path: str) -> None:
    max_tries = 2
    for attempt in range(1, max_tries + 1):
        try:
            subprocess.run(
                cmd,
                input=prompt.encode(),
                check=True,
                timeout=timeout or None,
                stderr=subprocess.PIPE,
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
