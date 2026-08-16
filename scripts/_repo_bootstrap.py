"""Make repository-local scripts runnable without an external PYTHONPATH."""
from __future__ import annotations

from pathlib import Path
import sys


def ensure_repo_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return root
