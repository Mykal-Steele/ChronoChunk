"""Unit tests for the sync's rules: course names, which files count, and how a transcript is cut."""
from sync.material import (
    CHUNK_SECONDS,
    Course,
    course_from_title,
    embedded_text,
    has_thai,
    local_day,
    recording_title,
    transcript_chunks,
    wanted,
)


def test_a_team_title_gives_the_course_code_name_and_other_codes():
    assert course_from_title("1-2569_CSC340/BIF651 ARTIFICIAL INTELLIGENCE", "1-2569") == Course(
        "CSC340", "Artificial Intelligence", ("BIF651",))
    assert course_from_title("1-2569_CSC535/CSC691 G.I BIG DATA ANALYTICS", "1-2569") == Course(
        "CSC535", "G.I Big Data Analytics", ("CSC691",))


def test_prerequisites_section_marks_and_channel_names_are_not_part_of_the_name():
    assert course_from_title("1-2569_CSC219 MODERN JAVA CSC122 (Pre) และ CSC202 (Pre)", "1-2569").name == "Modern Java"
    assert course_from_title("1-2569_LNG320(G.7) Content-based Language Learning", "1-2569") == Course(
        "LNG320", "Content-based Language Learning", ())
    mentoring = course_from_title("1-2569_CSC102 INTRODUCTION TO PROGRAMMING-Mentoring Session Group 3", "1-2569")
    assert (mentoring.code, mentoring.name) == ("CSC102", "Introduction To Programming")


def test_a_title_without_a_course_code_is_no_course():
    assert course_from_title("1-2569_Student Lounge", "1-2569") is None


def test_documents_of_this_semester_are_wanted():
    assert wanted("Class Materials", "/Week 6", "slides.pdf", "2026-09-18T03:00:00Z", "2026-07-01") == (True, "")
    assert wanted("Documents", "/General", "plan.DOCX", "2026-07-01T00:00:00Z", "2026-07-01") == (True, "")


def test_other_files_are_left_out_with_the_reason():
    start = "2026-07-01"
    assert wanted("Student Work", "/Submitted", "mine.pdf", "2026-09-01T00:00:00Z", start) == (False, "the student's own work")
    assert wanted("Documents", "/General/Recordings", "notes.pdf", "2026-09-01T00:00:00Z", start) == (False, "a recording")
    assert wanted("Class Materials", "/Week 2/Photos", "IMG_1.jpg", "2026-09-01T00:00:00Z", start) == (False, "type .jpg")
    assert wanted("Class Materials", "/", "README", "2026-09-01T00:00:00Z", start) == (False, "type unknown")
    assert wanted("Class Materials", "/2-2024", "old.pdf", "2025-01-20T00:00:00Z", start) == (False, "older than the semester")


def test_a_utc_time_becomes_the_bangkok_date():
    assert local_day("2026-08-04T02:00:49Z") == "2026-08-04"
    assert local_day("2026-08-04T18:30:00Z") == "2026-08-05"
    assert local_day("not a time") == ""


def test_a_recording_title_drops_what_teams_adds():
    assert recording_title("CSC340_20260804-20260804_090049-Meeting Recording.mp4") == "CSC340_20260804"
    assert recording_title("CSC219__20261005-20261005_132826-Meeting Transcript.mp4") == "CSC219__20261005"
    assert recording_title("Lecture 9 -- Oct 6.mp4") == "Lecture 9 -- Oct 6"


def _entry(second: int, text: str, length: int = 5) -> dict:
    def offset(total: int) -> str:
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}.5000000"
    return {"text": text, "startOffset": offset(second), "endOffset": offset(second + length)}


def test_a_transcript_is_cut_into_chunks_that_overlap_by_two_lines():
    entries = [_entry(second, f"line {number}") for number, second in enumerate(range(0, 200, 10))]
    chunks = transcript_chunks(entries)
    assert chunks[0] == ("0:00:00", "line 0 line 1 line 2 line 3 line 4 line 5 line 6 line 7")
    # The second chunk starts with the last two lines of the first
    assert chunks[1][0] == "0:01:00"
    assert chunks[1][1].startswith("line 6 line 7 line 8")
    assert all(text for _, text in chunks)
    assert CHUNK_SECONDS == 75


def test_every_line_of_a_transcript_ends_up_in_a_chunk():
    entries = [_entry(second, f"line{number}") for number, second in enumerate(range(0, 1000, 7))]
    said = " ".join(text for _, text in transcript_chunks(entries))
    assert all(f"line{number}" in said.split() for number in range(len(entries)))


def test_a_short_transcript_is_one_chunk_and_silence_is_dropped():
    assert transcript_chunks([_entry(0, "hello"), _entry(5, "  "), _entry(10, "there")]) == [("0:00:00", "hello there")]
    assert transcript_chunks([]) == []
    assert transcript_chunks([_entry(0, "")]) == []


def test_the_tail_that_only_repeats_the_overlap_is_not_a_chunk():
    # Exactly one full chunk: what is left afterwards is the two lines carried over
    entries = [_entry(second, f"line {number}") for number, second in enumerate(range(0, 80, 10))]
    assert len(transcript_chunks(entries)) == 1


def test_thai_is_noticed_only_when_there_is_enough_of_it():
    assert has_thai("หยิบไพ่ หยิบ 5 ใบ จากกองไพ่")
    assert not has_thai("We call this ค่า in Thai")
    assert not has_thai("Plain English with a naïve accent")


def test_the_embedded_text_names_where_the_chunk_is_from():
    course = Course("CSC340", "Artificial Intelligence", ())
    assert embedded_text(course, "slide", "4th--Pattern Recog.pdf", "Week 4", "page 18", "Gini Index") == (
        "CSC340 Artificial Intelligence | slide | 4th--Pattern Recog.pdf | Week 4 | page 18\nGini Index")
    assert embedded_text(course, "transcript", "CSC340_20260804", "", "0:11:21", "hello").startswith(
        "CSC340 Artificial Intelligence | transcript | CSC340_20260804 | 0:11:21\n")
