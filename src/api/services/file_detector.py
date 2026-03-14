"""File snapshot and change detection for tracking agent modifications."""

import os


def snapshot_files(directory: str) -> dict[str, float]:
    """Take a snapshot of file modification times in a directory."""
    snapshot = {}
    if not directory or not os.path.isdir(directory):
        return snapshot
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "sessions"]
        for f in files:
            if f.startswith("."):
                continue
            path = os.path.join(root, f)
            try:
                snapshot[path] = os.path.getmtime(path)
            except OSError:
                pass
    return snapshot


def detect_changed_files(before: dict[str, float], directory: str) -> list[str]:
    """Compare current files against a snapshot to find new/modified files."""
    changed = []
    if not directory or not os.path.isdir(directory):
        return changed
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "sessions"]
        for f in files:
            if f.startswith("."):
                continue
            path = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if path not in before or mtime > before[path]:
                changed.append(path)
    return changed
