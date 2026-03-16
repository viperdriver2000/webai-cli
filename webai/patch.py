"""Unified diff extraction and application."""
import re
import subprocess
from pathlib import Path


# Match any --- / +++ header, regardless of a/ b/ prefix or /dev/null
_DIFF_RE = re.compile(
    r"(--- .+?\n\+\+\+ .+?\n(?:[^\n]+\n?)+)",
    re.MULTILINE,
)


def _split_multi(diff_text: str) -> list[str]:
    """Split a multi-file diff block into one-per-file diffs."""
    lines = diff_text.splitlines(keepends=True)
    starts = [
        i for i in range(len(lines) - 1)
        if lines[i].startswith("--- ") and lines[i + 1].startswith("+++ ")
    ]
    if len(starts) <= 1:
        return [diff_text]
    return [
        "".join(lines[starts[n]: starts[n + 1] if n + 1 < len(starts) else len(lines)])
        for n in range(len(starts))
    ]


def extract_diffs(text: str) -> list[str]:
    """Extract unified diff blocks from a text response, one per file."""
    result = []
    for m in _DIFF_RE.finditer(text):
        result.extend(_split_multi(m.group(1)))
    return result


def _clean_path(p: str, prefix: str, cwd_s: str) -> str:
    p = p.removeprefix(prefix)
    return p.removeprefix(cwd_s).removeprefix(cwd_s.lstrip("/"))


def normalize_diff(diff_text: str, cwd: Path) -> str:
    """Normalize all diff headers to a/b prefix and relative paths."""
    cwd_s = str(cwd) + "/"
    lines = diff_text.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            src = lines[i][4:].rstrip("\n")
            tgt = lines[i + 1][4:].rstrip("\n")
            src_n = None if src == "/dev/null" else _clean_path(src, "a/", cwd_s)
            tgt_n = None if tgt == "/dev/null" else _clean_path(tgt, "b/", cwd_s)
            result.append(f"--- a/{src_n}\n" if src_n else "--- /dev/null\n")
            result.append(f"+++ b/{tgt_n}\n" if tgt_n else "+++ /dev/null\n")
            i += 2
        else:
            result.append(lines[i])
            i += 1
    return "".join(result)


def _parse_all_paths(diff_text: str) -> list[tuple[str | None, str | None]]:
    """Return all (source, target) filename pairs from a diff."""
    pairs, lines, i = [], diff_text.splitlines(), 0
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            src, tgt = lines[i][4:].rstrip(), lines[i + 1][4:].rstrip()
            pairs.append((
                None if src == "/dev/null" else src.removeprefix("a/"),
                None if tgt == "/dev/null" else tgt.removeprefix("b/"),
            ))
            i += 2
        else:
            i += 1
    return pairs


def _fix_hunk_counts(diff_text: str) -> str:
    """Recalculate @@ hunk header line counts from actual diff lines."""
    lines = diff_text.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*\n?)', line)
        if m:
            old_start, new_start, suffix = m.group(1), m.group(2), m.group(3)
            old_count = new_count = 0
            j = i + 1
            while j < len(lines) and not lines[j].startswith('@@'):
                c = lines[j][0] if lines[j].strip() else ' '
                if c == ' ':
                    old_count += 1
                    new_count += 1
                elif c == '-':
                    old_count += 1
                elif c == '+':
                    new_count += 1
                j += 1
            result.append(f'@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}')
            i += 1
        else:
            result.append(line)
            i += 1
    return ''.join(result)


def _safe_path(filename: str, cwd: Path) -> Path | None:
    """Return resolved Path if within cwd, else None."""
    try:
        p = (cwd / filename).resolve()
        p.relative_to(cwd.resolve())
        return p
    except ValueError:
        return None


def apply_diff(diff_text: str, cwd: Path) -> tuple[bool, str]:
    """Apply a unified diff via patch -p1. Returns (success, output)."""
    pairs = _parse_all_paths(diff_text)
    if not pairs:
        return False, "No diff headers found."

    for src, tgt in pairs:
        for name in filter(None, [src, tgt]):
            if _safe_path(name, cwd) is None:
                return False, f"Rejected: '{name}' is outside repository."

    # Single-file deletion: bypass patch to avoid content-mismatch issues
    if len(pairs) == 1 and pairs[0][1] is None and pairs[0][0]:
        src = pairs[0][0]
        path = _safe_path(src, cwd)
        subprocess.run(["git", "rm", "--cached", "--ignore-unmatch", src], cwd=cwd, capture_output=True)
        try:
            path.unlink()
            return True, f"rm {src}"
        except FileNotFoundError:
            return False, f"File not found: {src}"

    if not diff_text.endswith("\n"):
        diff_text += "\n"
    diff_text = _fix_hunk_counts(diff_text)
    result = subprocess.run(
        ["patch", "-p1", "--no-backup-if-mismatch", "--reject-file=/dev/null"],
        input=diff_text, text=True, capture_output=True, cwd=cwd,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, output

    msgs = [output] if output else []
    for src, tgt in pairs:
        if src is None and tgt:
            subprocess.run(["git", "add", tgt], cwd=cwd)
            msgs.append(f"git add {tgt}")
        elif tgt is None and src:  # deletion in multi-file diff
            path = _safe_path(src, cwd)
            subprocess.run(["git", "rm", "--cached", "--ignore-unmatch", src], cwd=cwd, capture_output=True)
            if path and path.exists() and path.stat().st_size == 0:
                path.unlink()
                msgs.append(f"rm {src}")
    return True, "\n".join(msgs)
