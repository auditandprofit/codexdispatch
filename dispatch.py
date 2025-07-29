"""Entry point for Codex dispatch tool."""
from codexdispatch import (
    parse_args,
    parse_codex_json,
    run_security_audit,
    main,
)

__all__ = [
    "parse_args",
    "parse_codex_json",
    "run_security_audit",
    "main",
]

if __name__ == "__main__":
    main()
