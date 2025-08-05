"""Phase mode only dispatcher package."""

from .args import parse_args
from .dispatcher import main, run_phase_mode

__all__ = ["parse_args", "main", "run_phase_mode"]
