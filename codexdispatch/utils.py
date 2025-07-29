import os
import subprocess
import logging
import json


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


def _invoke_codex(cmd: list[str], prompt: str, timeout: int | None, path: str) -> None:
    max_tries = 2
    for attempt in range(1, max_tries + 1):
        try:
            subprocess.run(
                cmd,
                input=prompt.encode(),
                check=True,
                timeout=timeout or None,
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
