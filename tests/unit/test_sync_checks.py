"""Grading a check run: what counts as a pass for each kind of message, and when a release may ship."""
from sync.checks.grading import VERDICTS, course_talk, gate, grade, parse_verdict


def _outcome(reply="ok", lookups=0, searches=0, sources=(), verdict=None, cited=()):
    return {"reply": reply, "lookups": lookups, "searches": searches, "sources": list(sources), "verdict": verdict,
            "cited": list(cited)}


def test_the_judges_answer_is_read_as_one_of_the_verdicts():
    assert parse_verdict('{"verdict": "correct", "why": "matches"}') == {"verdict": "correct", "why": "matches", "passages": {}}
    # Wrapped in a code fence, with the passages it looked at
    fenced = '```json\n{"verdict": "made_up", "why": "no passage says it", "passages": {"1": "related", "2": "unrelated"}}\n```'
    assert parse_verdict(fenced)["verdict"] == "made_up" and parse_verdict(fenced)["passages"] == {"1": "related", "2": "unrelated"}
    assert parse_verdict("not json at all")["verdict"] == "unreadable"
    assert parse_verdict('{"verdict": "excellent"}')["verdict"] == "unreadable"
    assert set(VERDICTS) == {"correct", "incomplete", "wrong_right_place", "wrong_place", "not_found", "made_up"}


def test_course_talk_is_noticed_in_a_reply():
    for reply in ("i couldnt find that in the course material", "add -c to look it up", "use -csc340 for that",
                  "not in ur slides", "-# Source: teams assignments list", "nothing in the saved material on that"):
        assert course_talk(reply), reply
    for reply in ("pasta, probably", "canberra", "recursion is a function calling itself, of course", "c is a language"):
        assert not course_talk(reply), reply


def test_plain_chat_passes_when_nothing_about_courses_happened():
    case = {"group": "chat"}
    assert grade(case, _outcome("canberra"))["ok"]
    assert not grade(case, _outcome("i couldnt find it in the course material"))["ok"]
    assert not grade(case, _outcome("canberra", lookups=1))["ok"]
    # Inside a session nothing is looked up for small talk either, though the material is there
    assert grade({"group": "session_chat"}, _outcome("pasta, probably"))["ok"]
    assert not grade({"group": "session_chat"}, _outcome("pasta", searches=1))["ok"]


def test_a_class_question_without_a_flag_passes_when_it_points_to_the_flag():
    case = {"group": "unflagged"}
    assert grade(case, _outcome("add -c at the end so i can look it up"))["ok"]
    assert not grade(case, _outcome("heuristic search uses h(n)"))["ok"]
    assert not grade(case, _outcome("add -c", lookups=1))["ok"]


def test_a_flagged_question_needs_its_lookup_its_source_and_a_good_verdict():
    case = {"group": "fact", "expect_source": ["Week 9"]}
    good = _outcome("draft intro", lookups=1, sources=["CBLL Week 9, page 47 (9 Oct 2026)"], verdict="correct")
    assert grade(case, good) == {"ok": True, "looked_up": True, "right_place": True, "verdict": "correct", "why": ""}
    assert not grade(case, dict(good, lookups=0))["ok"]
    wrong_place = grade(case, dict(good, sources=["Week 1 slides, page 2"]))
    assert not wrong_place["right_place"] and not wrong_place["ok"]
    assert grade(case, dict(good, verdict="incomplete"))["ok"]
    for verdict in ("wrong_right_place", "wrong_place", "made_up", "not_found", "unreadable"):
        assert not grade(case, dict(good, verdict=verdict))["ok"], verdict
    # A follow-up is answered from what was saved, so no new lookup is needed
    assert grade({"group": "followup", "expect_source": ["Week 9"]}, dict(good, lookups=0))["ok"]


def test_a_question_the_material_cannot_answer_passes_by_saying_so():
    for group in ("absent", "partial"):
        assert grade({"group": group}, _outcome("couldnt find that", lookups=1, verdict="not_found"))["ok"]
        assert not grade({"group": group}, _outcome("it is 42", lookups=1, verdict="made_up"))["ok"]
        assert not grade({"group": group}, _outcome("it is 42", lookups=1, verdict="correct"))["ok"]


def test_the_gate_holds_a_release_until_every_line_is_met():
    def result(group, ok=True, looked_up=True, right_place=True, verdict="correct", reply="x"):
        return {"group": group, "ok": ok, "looked_up": looked_up, "right_place": right_place, "verdict": verdict, "reply": reply}
    good = ([result("fact") for _ in range(18)] + [result("fact", ok=False, right_place=False, verdict="wrong_place")]
            + [result("absent", verdict="not_found"), result("chat", verdict=None), result("unflagged", verdict=None)])
    report = gate(good)
    assert report["passed"] and report["lines"]["right place"] == "18 of 19 (95%)"

    for bad, line in ((result("fact", ok=False, looked_up=False), "lookup on every flagged message"),
                      (result("fact", ok=False, verdict="made_up"), "nothing made up"),
                      (result("absent", ok=False, verdict="correct"), "says so when it is not there"),
                      (result("chat", ok=False, verdict=None), "no course talk in plain chat"),
                      (result("fact", reply="x\n-# Source: [name](<>)"), "no empty links")):
        report = gate(good + [bad])
        assert not report["passed"] and line in report["failed"], line
    # Under 90% in the right place fails too
    low = gate([result("fact") for _ in range(8)] + [result("fact", ok=False, right_place=False) for _ in range(2)])
    assert not low["passed"] and "right place" in low["failed"]
