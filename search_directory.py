"""Script to process files with OpenAI using web_search tool."""

from __future__ import annotations

import argparse
import os
from typing import Dict

from openai import OpenAI


def run_on_directory(
    input_dir: str,
    template: str,
    output_dir: str | None = None,
    client: OpenAI | None = None,
) -> Dict[str, str]:
    """Send each file's contents to the OpenAI API with a system template.

    Args:
        input_dir: Directory containing text files to process.
        template: Template to prepend as a system message.
        output_dir: If provided, responses are written to this directory as
            ``<filename>_response.txt``.
        client: Optional OpenAI client. A new client is created if not provided.

    Returns:
        Mapping of file name to response text.
    """
    if client is None:
        client = OpenAI()

    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)

    responses: Dict[str, str] = {}
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        messages = [
            {"role": "system", "content": template},
            {"role": "user", "content": content},
        ]
        response = client.responses.create(
            model="o3",
            input=messages,
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},
            service_tier="flex",
            reasoning={"effort": "high"},
        )

        text = getattr(response, "output_text", "")
        if not text:
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for part in getattr(item, "content", []) or []:
                        txt = getattr(part, "text", None)
                        if txt:
                            text += txt
        responses[name] = text

        if output_dir is not None:
            out_name = f"{os.path.splitext(name)[0]}_response.txt"
            out_path = os.path.join(output_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as out_f:
                out_f.write(text)
    return responses


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OpenAI web search over files in a directory")
    parser.add_argument("input_dir", help="Directory containing input files")
    parser.add_argument(
        "template_file",
        help="Path to file whose contents will be used as the system template",
    )
    parser.add_argument(
        "output_dir",
        help="Directory to write response text files",
    )
    args = parser.parse_args()

    with open(args.template_file, "r", encoding="utf-8") as tf:
        template = tf.read()

    run_on_directory(args.input_dir, template, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
