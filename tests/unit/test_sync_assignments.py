"""Unit tests for assignment rules: keeping course facts only, and turning them into chunks."""
from sync.assignments import (
    assignment_text,
    clean_html,
    course_facts,
    dedupe,
    due_day,
    listed,
    more_to_load,
    opened,
    parse_points,
    recorded,
)


def _item(id="a1", class_id="class-1", name="Lab 7 -- Classifier"):
    return {"id": id, "classId": class_id, "displayName": name, "dueDateTime": "2026-09-27T16:59:00Z",
            "closeDateTime": "2026-10-01T16:59:00Z", "status": "assigned",
            "webUrl": "https://example.test/assignment"}


def test_html_instructions_become_plain_text():
    assert clean_html({"content": "<div>Submit <b>2 images</b> before Sunday&nbsp;midnight.</div>", "contentType": "html"}) == \
        "Submit 2 images before Sunday midnight."
    assert clean_html({"content": "plain", "contentType": "text"}) == "plain"
    assert clean_html(None) == ""
    assert clean_html({"content": "", "contentType": "html"}) == ""


def test_an_assignment_listed_twice_is_kept_once():
    items = [_item("a1", "c1"), _item("a1", "c1"), _item("a2", "c1"), {"displayName": "no id"}]
    assert [i["id"] for i in dedupe(items)] == ["a1", "a2"]


def test_points_come_from_the_grading_block():
    assert parse_points({"@odata.type": "#microsoft.education.assignments.api.educationAssignmentPointsGradeType",
                         "maxPoints": 5}) == "5 points"
    assert parse_points(None) == ""
    assert parse_points({}) == ""


def test_assignment_text_holds_facts_and_no_personal_fields():
    detail = {"instructions": {"content": "<div>Follow the attached file.</div>", "contentType": "html"},
              "grading": {"maxPoints": 5},
              "resources": [{"resource": {"displayName": "Lab7.pptx"}}],
              "rubric": {"title": "Clarity"}} | _item()
    text = assignment_text(detail)
    assert "Lab 7 -- Classifier" in text
    assert "Due 2026-09-27" in text
    assert "Follow the attached file." in text
    assert "5 points" in text
    assert "Lab7.pptx" in text
    # Personal blocks never reach the text: submissions, members, user ids
    assert "student-id-1" not in text


def _raw(**changes):
    """An assignment as the class list sends it, personal fields and all."""
    return _item() | {"createdDateTime": "2026-09-22T05:02:05Z", "assignedDateTime": "2026-09-22T05:06:52Z",
                      "isCompleted": True, "assignTo": {"recipients": ["student-id-1"]},
                      "createdBy": {"user": {"id": "student-id-1"}}, "submissions": [{"id": "s1", "status": "submitted"}],
                      "submissionAggregates": {"submitted": 31}} | changes


def test_course_facts_carry_nothing_personal():
    [kept] = course_facts([_raw()], "2026-07-01")
    assert kept["displayName"] == "Lab 7 -- Classifier"
    assert kept["dueDateTime"] == "2026-09-27T16:59:00Z"
    for personal in ("isCompleted", "assignTo", "createdBy", "submissions", "submissionAggregates"):
        assert personal not in kept
    assert "student-id-1" not in str(kept)


def test_an_assignment_that_is_not_active_is_left_out():
    items = [_raw(id="a1"), _raw(id="a2", status="inactive"), _raw(id="a3", status="draft")]
    assert [item["id"] for item in course_facts(items, "2026-07-01")] == ["a1"]


def test_work_from_an_earlier_semester_is_left_out():
    old = _raw(id="old", createdDateTime="2025-09-01T03:00:00Z", assignedDateTime="2025-09-01T03:00:00Z",
               dueDateTime="2025-09-22T16:59:00Z", closeDateTime=None)
    assert [item["id"] for item in course_facts([old, _raw(id="new")], "2026-07-01")] == ["new"]


def test_the_due_date_decides_the_semester_when_there_is_one():
    # Written before the semester began, due inside it
    early = _raw(id="early", createdDateTime="2026-06-20T03:00:00Z", assignedDateTime="2026-06-25T03:00:00Z",
                 dueDateTime="2026-08-10T16:59:00Z")
    no_due = _raw(id="no-due", dueDateTime=None, closeDateTime=None)
    assert [item["id"] for item in course_facts([early, no_due], "2026-07-01")] == ["early", "no-due"]


def test_a_detail_fills_in_what_the_list_leaves_empty():
    listed = _raw(instructions=None, resources=None, grading=None)
    detail = _raw(instructions={"content": "<p>Train a classifier.</p>", "contentType": "html"},
                  grading={"maxPoints": 5}, resources=[{"resource": {"displayName": "Lab7.pptx", "createdBy": {"user": {"id": "student-id-1"}}}}])
    [kept] = course_facts([listed], "2026-07-01", details={"a1": detail})
    assert clean_html(kept["instructions"]) == "Train a classifier."
    assert parse_points(kept["grading"]) == "5 points"
    assert kept["resources"] == [{"resource": {"displayName": "Lab7.pptx"}}]
    assert "student-id-1" not in str(kept)


CLASS = "11111111-2222-4333-8444-555555555555"
ONE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
API = "/api/v1.0/edu"


def _answer(path, body, status=200):
    return {"method": "GET", "host": "assignments.edu.cloud.microsoft", "path": path, "query": "", "status": status, "body": body}


def test_only_the_class_list_and_an_assignment_itself_are_recorded():
    assert recorded(f"{API}/classes/{CLASS}/assignments")
    assert recorded(f"{API}/classes/{CLASS}/assignments/{ONE}")
    # Whatever belongs to a person is never read off the page
    assert not recorded(f"{API}/classes/{CLASS}/assignments/{ONE}/submissions")
    assert not recorded(f"{API}/classes/{CLASS}/members")
    assert not recorded(f"{API}/me/work")
    assert not recorded(f"{API}/classes/{CLASS}/defaultsAndSettings")


def test_listed_gathers_one_class_from_every_tab_it_loaded():
    answers = [_answer(f"{API}/classes/{CLASS}/assignments", {"value": []}),
               _answer(f"{API}/classes/{CLASS}/assignments", {"value": [_raw(id="a1"), _raw(id="a2")]}),
               _answer(f"{API}/classes/{CLASS}/assignments", {"value": [_raw(id="a2"), _raw(id="a3")]}),
               _answer(f"{API}/classes/another-class/assignments", {"value": [_raw(id="b1")]}),
               _answer(f"{API}/classes/{CLASS}/assignments", {"error": "no"}, status=500),
               _answer(f"{API}/classes/{CLASS}/assignments/{ONE}", _raw(id=ONE))]
    assert [entry["id"] for entry in listed(answers, CLASS)] == ["a1", "a2", "a3"]


def test_opened_finds_the_assignment_that_was_opened():
    answers = [_answer(f"{API}/classes/{CLASS}/assignments", {"value": [_raw(id=ONE)]}),
               _answer(f"{API}/classes/{CLASS}/assignments/{ONE}", {"error": "busy"}, status=503),
               _answer(f"{API}/classes/{CLASS}/assignments/{ONE}", _raw(id=ONE, displayName="Lab 7"))]
    assert opened(answers, CLASS, ONE)["displayName"] == "Lab 7"
    assert opened(answers, CLASS, "some-other-id") is None
    assert opened(answers[:2], CLASS, ONE) is None


def test_more_to_load_follows_the_last_list_answer():
    full = _answer(f"{API}/classes/{CLASS}/assignments", {"value": [_raw()], "@odata.nextLink": "https://example.test/next"})
    last = _answer(f"{API}/classes/{CLASS}/assignments", {"value": [_raw(id="a9")]})
    assert more_to_load([full], CLASS)
    assert not more_to_load([full, last], CLASS)
    assert not more_to_load([], CLASS)


def test_due_time_is_told_in_bangkok_time():
    # 18:30 UTC is already the next day in Bangkok
    text = assignment_text(_item() | {"dueDateTime": "2026-09-27T18:30:00Z", "closeDateTime": "2026-10-01T16:59:00Z"})
    assert "Due 2026-09-28 01:30 Bangkok time" in text
    assert "Closes 2026-10-01 23:59 Bangkok time" in text
    assert due_day(_item() | {"dueDateTime": "2026-09-27T18:30:00Z"}) == "2026-09-28"
    assert due_day(_item() | {"dueDateTime": None}) == ""


def test_style_rules_inside_instructions_are_not_text():
    styled = ('<style>.c0{max-width: 100%; object-fit: contain;}.c1{list-style-type: "i) ";}</style>'
              '<div class="c0">Explore the data<br>and report</div><script>track()</script>')
    assert clean_html({"content": styled, "contentType": "html"}) == "Explore the data and report"
    # Instructions that are only a picture have no text to keep
    assert clean_html({"content": '<style>.c0{max-width: 100%;}</style><img class="c0" src="x.png">', "contentType": "html"}) == ""
