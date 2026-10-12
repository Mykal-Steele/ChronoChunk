"""
V5: assignments. The Assignments app in Teams is opened in the saved profile and the
answers it loads are kept. This walks all three tabs, scrolling so every page of each
list loads, then opens one finished assignment to see what its detail call returns.
Prints counts and field names, not the user's own submissions or grades.
"""
import re
from collections import Counter

from sync.session import Recorder, browser, open_teams

APP_FRAME = 'iframe[src*="assignments.edu.cloud.microsoft"]'


def main() -> None:
    with browser() as context:
        recorder = Recorder(context, "assignments.edu.cloud.microsoft", lambda path: "/api/" in path)
        page = open_teams(context)
        page.get_by_role("button", name=re.compile(r"^Assignments")).first.click()
        frame = page.frame_locator(APP_FRAME)
        frame.get_by_role("tab").first.wait_for(timeout=60_000)
        page.wait_for_timeout(4000)

        found: dict = {}
        for tab in frame.get_by_role("tab").all_inner_texts():
            mark = len(recorder.answers)
            frame.get_by_role("tab", name=re.compile(tab)).first.click()
            page.wait_for_timeout(6000)
            for _ in range(8):
                page.mouse.move(650, 500)
                page.mouse.wheel(0, 6000)
                page.wait_for_timeout(1200)
            lists = [a for a in recorder.answers[mark:] if a["path"].endswith("/me/work")]
            found[tab] = [item for answer in lists for item in (answer["body"].get("value") or [])]
            print(f"{tab}: {len(found[tab])} assignments from {len(lists)} list calls, "
                  f"classes {dict(Counter(item['classId'][:8] for item in found[tab]))}")

        finished = found.get("Completed") or []
        if finished:
            mark = len(recorder.answers)
            frame.get_by_text(finished[0]["displayName"][:30], exact=False).first.click()
            page.wait_for_timeout(9000)
            for answer in recorder.answers[mark:]:
                body = answer["body"]
                path = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "{id}", answer["path"])
                print(f"  {answer['status']} {path}")
                if isinstance(body, dict) and "instructions" in body:
                    resources = [r.get("resource", {}).get("@odata.type", "").rsplit(".", 1)[-1] for r in body.get("resources") or []]
                    print(f"     instructions {len((body['instructions'] or {}).get('content') or '')} chars, "
                          f"resources {resources}, points {(body.get('grading') or {}).get('maxPoints')}, "
                          f"rubric {bool(body.get('rubric'))}")


if __name__ == "__main__":
    main()
