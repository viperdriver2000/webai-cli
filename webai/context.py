"""File loading and concatenation for context/upload."""
import fnmatch
import subprocess
from pathlib import Path


def load_files(path: str, glob: str = "*") -> str:
    """Load files from path (file or dir) matching glob, return formatted string."""
    p = Path(path).expanduser()
    if p.is_file():
        return _format_file(p)
    if p.is_dir():
        files = sorted(f for f in p.rglob(glob) if f.is_file())
        return "\n".join(_format_file(f) for f in files)
    raise FileNotFoundError(f"Path not found: {path}")


def load_git_context(cwd: Path) -> str:
    """Return all git-tracked files as formatted context string."""
    r = subprocess.run(["git", "ls-files"], cwd=cwd, capture_output=True, text=True)
    return "\n".join(_format_file(cwd / f) for f in r.stdout.splitlines() if f)


def _format_file(path: Path) -> str:
    try:
        content = path.read_text(errors="replace")
    except Exception as e:
        content = f"<error reading file: {e}>"
    return f"=== {path} ===\n{content}\n"
