"""What counts as a study request, and what the prompt asks for when it is one."""
import pytest

from src.study import MAX_STUDY_ITEMS, StudyRequest, study_prompt, study_request, study_search_words, wants_detail


@pytest.mark.parametrize("text, kind, count", [
    ("make 30 multiple choice for my ai exam", "quiz", 30),
    ("30 mcqs of an exam", "quiz", 30),
    ("give me 25 practice questions on deadlocks", "quiz", 25),
    ("make a quiz for csc340", "quiz", 0),
    ("i need a study guide for the os final", "guide", 0),
    ("write 40 revision questions", "quiz", 40),
    ("quiz me on deadlocks", "quiz", 0),
    ("make 20 short answer questions", "short", 20),
    ("20 Qs on pca", "quiz", 20),
])
def test_study_requests_are_spotted(text, kind, count):
    request = study_request(text)
    assert request == StudyRequest(kind, count)


@pytest.mark.parametrize("text", [
    "i got 30 questions wrong on the test",
    "the quiz was hard",
    "what is a deadlock",
    "send the slides",
    "my study notes on deadlocks are a mess",
    "can you help me with these 5 questions",
    "",
])
def test_ordinary_chat_is_not_a_study_request(text):
    assert study_request(text) is None


def test_a_quiz_with_no_number_gets_a_sensible_default():
    assert study_request("make me a quiz").items == 10


def test_a_number_over_the_cap_is_not_taken_as_a_request_for_that_many():
    assert study_request(f"make {MAX_STUDY_ITEMS + 1} mcqs") == StudyRequest("quiz", MAX_STUDY_ITEMS)
    assert study_request("make 0 mcqs") is None


def test_the_search_leaves_out_the_quiz_instructions():
    request = study_request("make 30 multiple choice questions about deadlocks for my os exam")
    assert study_search_words("make 30 multiple choice questions about deadlocks for my os exam", request) == "deadlocks os exam"


def test_the_number_is_removed_even_when_it_is_the_exam_number():
    request = StudyRequest("quiz", 30)
    assert study_search_words("30 mcqs for ai exam 2", request) == "ai exam 2"


def test_a_short_request_does_not_get_study_rules():
    assert study_request("yo") is None


def test_the_quiz_prompt_fixes_the_shape_and_keeps_the_voice():
    prompt = study_prompt(StudyRequest("quiz", 30))
    assert "exactly 30 questions" in prompt
    assert "numbered 1 to 30" in prompt
    assert "answer key" in prompt
    assert "voice stays exactly the same" in prompt
    assert "never raw LaTeX" in prompt


def test_the_guide_prompt_asks_for_examples_and_the_voice():
    prompt = study_prompt(StudyRequest("guide", 0))
    assert "worked example" in prompt
    assert "voice stays exactly the same" in prompt
    assert "=== GUIDE COMPLETE ===" in prompt
    assert "Write exactly" not in prompt


def test_the_short_prompt_asks_for_model_answers_not_an_answer_key():
    prompt = study_prompt(StudyRequest("short", 10))
    assert "Model answers" in prompt
    assert "No multiple-choice options" in prompt


@pytest.mark.parametrize("text, expected", [
    ("explain deadlock in detail", True),
    ("give me a step-by-step on paging", True),
    ("what is a deadlock", False),
])
def test_wants_detail(text, expected):
    assert wants_detail(text) is expected

