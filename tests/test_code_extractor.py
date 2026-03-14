"""Tests for code block extraction from agent output."""

from src.api.services.code_extractor import extract_inline_code, LANGUAGE_EXTENSIONS


def test_extract_single_python_block():
    output = "Here's the code:\n```python\ndef hello():\n    print('hello')\n```"
    blocks = extract_inline_code(output)
    assert len(blocks) == 1
    lang, code, filename = blocks[0]
    assert lang == "python"
    assert "def hello" in code
    assert filename == "code.py"


def test_extract_multiple_blocks():
    output = (
        "```python\nprint('a')\n```\nAnd the JS:\n```javascript\nconsole.log('b');\n```"
    )
    blocks = extract_inline_code(output)
    assert len(blocks) == 2
    assert blocks[0][2] == "code.py"
    assert blocks[1][2] == "code_1.js"


def test_skip_short_code_blocks():
    output = "```python\nx\n```"
    blocks = extract_inline_code(output)
    assert len(blocks) == 0


def test_unknown_language_gets_txt_extension():
    output = "```unknownlang\nsome long content here that is enough\n```"
    blocks = extract_inline_code(output)
    assert len(blocks) == 1
    assert blocks[0][2] == "code.txt"


def test_no_language_specified():
    output = "```\nsome generic code content here\n```"
    blocks = extract_inline_code(output)
    assert len(blocks) == 1
    assert blocks[0][0] == ""
    assert blocks[0][2] == "code.txt"


def test_language_extension_map_coverage():
    """Ensure common languages are mapped."""
    assert ".py" in LANGUAGE_EXTENSIONS.values()
    assert ".js" in LANGUAGE_EXTENSIONS.values()
    assert ".ts" in LANGUAGE_EXTENSIONS.values()
    assert ".go" in LANGUAGE_EXTENSIONS.values()
    assert ".rs" in LANGUAGE_EXTENSIONS.values()
