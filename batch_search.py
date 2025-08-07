"""Batch search mode: send files to OpenAI with forced web search."""

import argparse
import json
import os

from openai import OpenAI


def batch_search(input_dir: str, output_dir: str, model: str = "gpt-4o") -> None:
    """Send contents of files in ``input_dir`` to OpenAI and write responses.

    Each file's content is passed to the Responses API with web search forced
    via ``tool_choice``. Results are written to ``output_dir`` using the
    original filename with ``_response.json`` appended.
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
        )

        out_filename = f"{os.path.splitext(filename)[0]}_response.json"
        out_path = os.path.join(output_dir, out_filename)

        with open(out_path, "w", encoding="utf-8") as out_f:
            json.dump(response, out_f, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch web search using OpenAI")
    parser.add_argument("input_dir", help="Directory containing input files")
    parser.add_argument("output_dir", help="Directory to write responses")
    parser.add_argument("--model", default="gpt-4o", help="Model name to use")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_search(args.input_dir, args.output_dir, model=args.model)


if __name__ == "__main__":
    main()
