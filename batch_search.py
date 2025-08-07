"""Batch search mode: send files to OpenAI with forced web search."""

import argparse
import os
from typing import Any

from openai import OpenAI


def _extract_output_text(response: Any) -> str:
    text = getattr(response, "output_text", "")
    if text:
        return text
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "message":
            for part in getattr(item, "content", []) or []:
                txt = getattr(part, "text", None)
                if txt:
                    text += txt
    if not text and isinstance(response, dict):
        for item in response.get("output", []) or []:
            if item.get("type") == "message":
                for part in item.get("content", []) or []:
                    txt = part.get("text")
                    if txt:
                        text += txt
    return text


def batch_search(input_dir: str, output_dir: str, model: str = "o3") -> None:
    """Send contents of files in ``input_dir`` to OpenAI and write responses.

    Each file's content is passed to the Responses API with web search forced
    via ``tool_choice``. Results are written to ``output_dir`` using the
    original filename with ``_response.txt`` appended.
    """

    client = OpenAI()
    os.makedirs(output_dir, exist_ok=True)

    for filename in os.listdir(input_dir):
        in_path = os.path.join(input_dir, filename)
        if not os.path.isfile(in_path):
            continue

        with open(in_path, "r", encoding="utf-8") as f:
            text = f.read()

        response = client.responses.create(
            model=model,
            input=text,
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},
            service_tier="flex",
            reasoning={"effort": "high"},
        )
        output_text = _extract_output_text(response)

        out_filename = f"{os.path.splitext(filename)[0]}_response.txt"
        out_path = os.path.join(output_dir, out_filename)

        with open(out_path, "w", encoding="utf-8") as out_f:
            out_f.write(output_text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch web search using OpenAI")
    parser.add_argument("input_dir", help="Directory containing input files")
    parser.add_argument("output_dir", help="Directory to write responses")
    parser.add_argument("--model", default="o3", help="Model name to use")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_search(args.input_dir, args.output_dir, model=args.model)


if __name__ == "__main__":
    main()
