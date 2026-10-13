"""The course flag: when a message is about course material, and which course it means."""
from src.course_scope import course_scope, search_words

COURSES = [
    {"code": "CSC102", "name": "Introduction To Programming", "aliases": ["intro to programming", "intro programming"]},
    {"code": "CSC319", "name": "Object-Oriented Design", "aliases": ["oo", "oo class", "oo design", "object oriented"]},
    {"code": "CSC340", "name": "Artificial Intelligence", "aliases": ["BIF651", "ai", "ai class"]},
    {"code": "CSC345", "name": "Data Science", "aliases": ["BIF633", "data sci", "ds"]},
    {"code": "CSC535", "name": "Big Data Analytics", "aliases": ["CSC691", "big data"]},
]


def test_a_message_without_a_flag_is_not_a_course_message():
    for message in ("yo whats up", "what did csc340 say about agents", "did microsoft rewrite windows in rust",
                    "of-course not", "the pre-course survey was long", "i took -courses last year"):
        scope = course_scope(message, COURSES)
        assert not scope.flagged and scope.codes == [] and scope.unknown == []
        assert scope.text == message


def test_a_code_flag_names_the_course_and_is_taken_out_of_the_message():
    scope = course_scope("what is a heuristic -csc340", COURSES)
    assert scope.flagged and scope.codes == ["CSC340"]
    assert scope.text == "what is a heuristic"
    # Anywhere in the message, in any case
    assert course_scope("-CSC340 what is a heuristic", COURSES).codes == ["CSC340"]
    assert course_scope("what is -Csc340 a heuristic", COURSES).text == "what is a heuristic"


def test_several_code_flags_and_a_code_alias():
    scope = course_scope("compare the grading -csc340 -bif633", COURSES)
    assert scope.codes == ["CSC340", "CSC345"]
    assert scope.text == "compare the grading"
    assert course_scope("x -csc340 -CSC340", COURSES).codes == ["CSC340"]


def test_a_code_the_file_does_not_hold_is_reported():
    scope = course_scope("when is the exam -csc999", COURSES)
    assert scope.flagged and scope.codes == [] and scope.unknown == ["CSC999"]
    mixed = course_scope("when is the exam -csc999 -csc340", COURSES)
    assert mixed.codes == ["CSC340"] and mixed.unknown == ["CSC999"]


def test_the_course_flag_reads_the_course_from_the_message():
    scope = course_scope("what to study for my ai class -course", COURSES)
    assert scope.flagged and scope.codes == ["CSC340"]
    assert scope.text == "what to study for my ai class"
    assert course_scope("-course when is the oo class midterm", COURSES).codes == ["CSC319"]
    assert course_scope("what did data science say about pca -course", COURSES).codes == ["CSC345"]
    assert course_scope("csc 340 midterm topics -course", COURSES).codes == ["CSC340"]
    assert course_scope("bif633 lab 4 -COURSE", COURSES).codes == ["CSC345"]


def test_the_course_flag_forgives_a_typo_in_a_long_name():
    assert course_scope("what is on the artifical inteligence final -course", COURSES).codes == ["CSC340"]
    assert course_scope("data sceince lab 7 -course", COURSES).codes == ["CSC345"]
    assert course_scope("object orinted design patterns -course", COURSES).codes == ["CSC319"]


def test_a_short_nickname_only_matches_as_a_whole_word():
    # "ai" is inside "explain" and "said", "oo" inside "good", "ds" inside "words"
    scope = course_scope("explain what the teacher said in good words -course", COURSES)
    assert scope.flagged and scope.codes == []


def test_the_course_flag_with_no_course_named_means_every_course():
    scope = course_scope("whats due this week -course", COURSES)
    assert scope.flagged and scope.codes == [] and scope.unknown == []
    assert scope.text == "whats due this week"


def test_two_courses_named_are_both_in_scope():
    assert course_scope("how do ai and data science overlap -course", COURSES).codes == ["CSC340", "CSC345"]


def test_a_code_flag_wins_over_what_the_message_names():
    assert course_scope("is this like the ai class -csc345", COURSES).codes == ["CSC345"]
    assert course_scope("is this like the ai class -course -csc345", COURSES).codes == ["CSC345"]


def test_a_typo_is_only_forgiven_in_a_long_word():
    # "io class" is one letter from "ai class", and "an int" is close to nothing a course is called
    assert course_scope("which java.io class wraps a stream -course", COURSES).codes == []
    assert course_scope("how is an int stored in the programing course -course", COURSES).codes == []
    assert course_scope("intro to programing arrays -course", COURSES).codes == ["CSC102"]


def test_a_course_code_is_never_matched_by_a_near_miss():
    # CSC491 is one character from CSC691, which is another course
    assert course_scope("what did csc491 say about subnets -course", COURSES).codes == []
    assert course_scope("what did csc691 say about hadoop -course", COURSES).codes == ["CSC535"]


def test_search_words_leave_out_the_course_and_the_words_about_asking():
    assert search_words("csc319 lecture: what did the teacher say about checked exceptions", COURSES, ["CSC319"]) == \
        "what did the about checked exceptions"
    assert search_words("what did the AI class say about the french revolution", COURSES, ["CSC340"]) == \
        "what did the about the french revolution"
    assert search_words("explain the slides on PCA in Data Science", COURSES, ["CSC345"]) == "the on PCA in"
    assert search_words("csc 340 a* search", COURSES, ["CSC340"]) == "a* search"


def test_search_words_keep_what_is_not_the_course_in_scope():
    # Java is a topic here, and the object oriented course is not the one being asked about
    assert search_words("how is object oriented code tested in the ai class", COURSES, ["CSC340"]) == \
        "how is object oriented code tested in the"
    assert search_words("LNG320 งานพรีเซนต์กลุ่มต้องยาวกี่นาที", COURSES, []) == "LNG320 งานพรีเซนต์กลุ่มต้องยาวกี่นาที"
    assert search_words("csc345 งานพรีเซนต์กลุ่มต้องยาวกี่นาที", COURSES, ["CSC345"]) == "งานพรีเซนต์กลุ่มต้องยาวกี่นาที"


def test_search_words_fall_back_to_the_message_when_nothing_is_left():
    assert search_words("the ai class lecture", COURSES, ["CSC340"]) == "the"
    assert search_words("ai class", COURSES, ["CSC340"]) == "ai class"
