"""Tests for question extraction from agent output."""

from src.api.services.question_extractor import extract_question


def test_extract_explicit_question_marker():
    output = "I've analyzed the code.\nQUESTION: What database should I use?"
    assert extract_question(output) == "What database should I use?"


def test_extract_question_marker_requires_uppercase():
    """The QUESTION: marker is case-sensitive (must be uppercase)."""
    output = "question: Which framework do you prefer?"
    assert extract_question(output) is None

    output_upper = "QUESTION: Which framework do you prefer?"
    assert extract_question(output_upper) == "Which framework do you prefer?"


def test_extract_please_clarify():
    output = "I'm not sure about the requirements.\nPlease clarify the expected output format."
    result = extract_question(output)
    assert result is not None
    assert "clarify" in result.lower()


def test_extract_need_more_information():
    output = "I need more information about the API endpoints."
    result = extract_question(output)
    assert result is not None
    assert "need more information" in result.lower()


def test_extract_before_i_proceed():
    output = "Before I can proceed, I need the database credentials."
    result = extract_question(output)
    assert result is not None
    assert "before" in result.lower()


def test_extract_could_you_provide():
    output = "Could you please provide the file path?"
    result = extract_question(output)
    assert result is not None
    assert "could you" in result.lower()


def test_no_question_in_normal_output():
    output = "I've created the file successfully.\nThe todo app is ready."
    assert extract_question(output) is None


def test_no_question_empty_string():
    assert extract_question("") is None
