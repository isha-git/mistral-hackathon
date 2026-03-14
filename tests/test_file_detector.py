"""Tests for file snapshot and change detection."""

import os
import tempfile
import time

from src.api.services.file_detector import snapshot_files, detect_changed_files


def test_snapshot_empty_directory():
    with tempfile.TemporaryDirectory() as d:
        snap = snapshot_files(d)
        assert snap == {}


def test_snapshot_captures_files():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "test.py")
        with open(path, "w") as f:
            f.write("print('hello')")

        snap = snapshot_files(d)
        assert path in snap


def test_snapshot_skips_hidden_files():
    with tempfile.TemporaryDirectory() as d:
        hidden = os.path.join(d, ".hidden")
        with open(hidden, "w") as f:
            f.write("secret")

        snap = snapshot_files(d)
        assert hidden not in snap


def test_snapshot_skips_hidden_dirs():
    with tempfile.TemporaryDirectory() as d:
        hidden_dir = os.path.join(d, ".git")
        os.makedirs(hidden_dir)
        hidden_file = os.path.join(hidden_dir, "config")
        with open(hidden_file, "w") as f:
            f.write("git config")

        snap = snapshot_files(d)
        assert hidden_file not in snap


def test_snapshot_skips_sessions_dir():
    with tempfile.TemporaryDirectory() as d:
        sessions_dir = os.path.join(d, "sessions")
        os.makedirs(sessions_dir)
        session_file = os.path.join(sessions_dir, "data.json")
        with open(session_file, "w") as f:
            f.write("{}")

        snap = snapshot_files(d)
        assert session_file not in snap


def test_snapshot_nonexistent_directory():
    snap = snapshot_files("/nonexistent/path")
    assert snap == {}


def test_snapshot_empty_string():
    snap = snapshot_files("")
    assert snap == {}


def test_detect_new_file():
    with tempfile.TemporaryDirectory() as d:
        before = snapshot_files(d)

        new_file = os.path.join(d, "new.py")
        with open(new_file, "w") as f:
            f.write("new content")

        changed = detect_changed_files(before, d)
        assert new_file in changed


def test_detect_modified_file():
    with tempfile.TemporaryDirectory() as d:
        existing = os.path.join(d, "existing.py")
        with open(existing, "w") as f:
            f.write("original")

        before = snapshot_files(d)
        time.sleep(0.05)  # Ensure mtime differs

        with open(existing, "w") as f:
            f.write("modified")

        changed = detect_changed_files(before, d)
        assert existing in changed


def test_detect_no_changes():
    with tempfile.TemporaryDirectory() as d:
        existing = os.path.join(d, "stable.py")
        with open(existing, "w") as f:
            f.write("stable")

        before = snapshot_files(d)
        changed = detect_changed_files(before, d)
        assert changed == []
