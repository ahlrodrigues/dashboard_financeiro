from __future__ import annotations

import subprocess
from pathlib import Path


VERSION = "1.0.1"


def get_git_commit_short() -> str:
    base_dir = Path(__file__).resolve().parent
    try:
        resultado = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""

    return resultado.stdout.strip()


def get_dashboard_version_label() -> str:
    commit = get_git_commit_short()
    if not commit:
        return f"V{VERSION}"
    return f"V{VERSION} ({commit})"