"""Extract clarification questions from agent output."""

import re

CLARIFICATION_PATTERNS = [
    r"please clarify",
    r"need more information",
    r"missing details",
    r"before I (?:can |)proceed",
    r"could you (?:please )?(?:provide|specify|clarify)",
]


def extract_question(output: str) -> str | None:
    """Extract question from output if agent is explicitly asking for clarification."""
    if "QUESTION:" in output:
        match = re.search(r"QUESTION:\s*(.+?)(?:\n|$)", output, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    for pattern in CLARIFICATION_PATTERNS:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            for line in output.split("\n"):
                if re.search(pattern, line, re.IGNORECASE):
                    return line.strip()
            return match.group(0).strip()

    return None
