"""Extract fenced code blocks from agent output."""

import re

from src.api.config.settings import get_settings

LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "html": ".html",
    "css": ".css",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
    "ruby": ".rb",
    "shell": ".sh",
    "bash": ".sh",
    "sql": ".sql",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yaml",
    "xml": ".xml",
    "c": ".c",
    "cpp": ".cpp",
}


def extract_inline_code(output: str) -> list[tuple[str, str, str]]:
    """Extract fenced code blocks from agent output.

    Returns list of (language, code, filename) tuples.
    """
    settings = get_settings()
    blocks = []
    pattern = r"```(\w+)?\s*\n(.*?)```"
    for match in re.finditer(pattern, output, re.DOTALL):
        lang = (match.group(1) or "").strip().lower()
        code = match.group(2).strip()
        if not code or len(code) < settings.min_code_block_length:
            continue

        ext = LANGUAGE_EXTENSIONS.get(lang, ".txt")
        filename = f"code{ext}" if len(blocks) == 0 else f"code_{len(blocks)}{ext}"
        blocks.append((lang, code, filename))

    return blocks
