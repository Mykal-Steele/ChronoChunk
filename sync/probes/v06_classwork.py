"""
V4 (older posts) and V6 (Classwork): open every class channel in the saved profile.

For each class it scrolls the General channel to the top and counts how many threads
the browser's store holds before and after, then opens the team's Classwork page and
reports which calls it made and whether it shows any modules. Opening a channel marks
it as read, the same as opening it by hand. Prints counts only, no post text.
"""
import re
from urllib.parse import quote

from sync.session import Recorder, browser, open_teams

PREFIX = "1-2569"
# A channel is at its top when this many scroll rounds in a row load nothing new
QUIET_ROUNDS = 3

STORE = """async (channel) => {
  const databases = await indexedDB.databases();
  const rows = async (pattern, store) => {
    const found = databases.find(d => pattern.test(d.name));
    if (!found) return [];
    const db = await new Promise((ok, fail) => { const r = indexedDB.open(found.name); r.onsuccess = () => ok(r.result); r.onerror = () => fail(r.error); });
    const all = await new Promise(ok => { const r = db.transaction(store).objectStore(store).getAll(); r.onsuccess = () => ok(r.result); r.onerror = () => ok([]); });
    db.close();
    return all;
  };
  if (!channel) {
    const conversations = await rows(/:conversation-manager:/, 'conversations');
    return conversations.filter(c => c.threadProperties).map(c => ({
      id: c.id, team: c.threadProperties.spaceThreadTopic, group: c.threadProperties.groupId}));
  }
  const chains = (await rows(/Teams:replychain-manager:/, 'replychains')).filter(c => c.conversationId === channel);
  const times = chains.map(c => c.originalArrivalTime).filter(Boolean);
  return {threads: chains.length, oldest: times.length ? new Date(Math.min(...times)).toISOString().slice(0, 10) : null};
}"""


def scroll_to_top(page, channel: str) -> dict:
    last, quiet = None, 0
    for _ in range(60):
        page.mouse.move(700, 400)
        for _ in range(6):
            page.mouse.wheel(0, -4000)
            page.wait_for_timeout(250)
        page.wait_for_timeout(1500)
        now = page.evaluate(STORE, channel)
        quiet = quiet + 1 if now == last else 0
        if quiet >= QUIET_ROUNDS:
            break
        last = now
    return last or {}


def main() -> None:
    with browser() as context:
        recorder = Recorder(context, "edu.cloud.microsoft", lambda path: "/api/" in path)
        page = open_teams(context)
        page.wait_for_timeout(6000)
        channels = sorted((c for c in page.evaluate(STORE, None) if (c["team"] or "").startswith(PREFIX)),
                          key=lambda c: c["team"])
        for channel in channels:
            before = page.evaluate(STORE, channel["id"])
            page.goto(f"https://teams.microsoft.com/l/channel/{quote(channel['id'], safe='')}/General?groupId={channel['group']}")
            page.get_by_role("tab", name="Posts").first.wait_for(timeout=90_000)
            page.wait_for_timeout(4000)
            after = scroll_to_top(page, channel["id"])
            print(f"{channel['team'][:44]:<44} threads {before['threads']:>3} -> {after.get('threads', 0):>3}, "
                  f"oldest {before['oldest']} -> {after.get('oldest')}", flush=True)

            mark = len(recorder.answers)
            link = page.get_by_text("Classwork", exact=True).first
            if not link.is_visible():
                print("      no Classwork link")
                continue
            link.click()
            page.wait_for_timeout(12000)
            calls = [re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "{id}", a["path"]) for a in recorder.answers[mark:]]
            modules = [a["body"] for a in recorder.answers[mark:] if a["path"].rstrip("/").endswith("/modules")]
            counts = [len(body.get("value") or []) for body in modules if isinstance(body, dict)]
            words = [len(frame.locator("body").inner_text(timeout=5000).split())
                     for frame in page.frames if "edu.cloud.microsoft" in frame.url]
            print(f"      Classwork: module lists {counts}, words on the page {words}, calls {sorted(set(calls))}", flush=True)


if __name__ == "__main__":
    main()
