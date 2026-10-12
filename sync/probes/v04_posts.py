"""
V4: channel posts. Teams loads them in a background worker, where a script cannot see
the network calls, and keeps them in the browser's own database (IndexedDB). This reads
that database and reports, per class channel, how many posts and replies are there.
Prints counts and message kinds only, no post text.
"""
from collections import Counter

from sync.session import browser, open_teams

PREFIX = "1-2569"

READ_STORES = """async () => {
  const databases = await indexedDB.databases();
  const rows = async (pattern, store) => {
    const found = databases.find(d => pattern.test(d.name));
    if (!found) return [];
    const db = await new Promise((ok, fail) => { const r = indexedDB.open(found.name); r.onsuccess = () => ok(r.result); r.onerror = () => fail(r.error); });
    const all = await new Promise(ok => { const r = db.transaction(store).objectStore(store).getAll(); r.onsuccess = () => ok(r.result); r.onerror = () => ok([]); });
    db.close();
    return all;
  };
  const conversations = await rows(/:conversation-manager:/, 'conversations');
  const chains = await rows(/Teams:replychain-manager:/, 'replychains');
  return {
    channels: conversations.filter(c => c.threadProperties).map(c => ({
      id: c.id, team: c.threadProperties.spaceThreadTopic, channel: c.threadProperties.topic,
      group: c.threadProperties.groupId, folder: c.threadProperties.channelDocsFolderRelativeUrl})),
    chains: chains.map(chain => ({
      channel: chain.conversationId, started: chain.originalArrivalTime,
      kinds: Object.values(chain.messageMap || {}).map(m => m.messageType)})),
  };
}"""


def main() -> None:
    with browser() as context:
        page = open_teams(context)
        page.wait_for_timeout(8000)
        data = page.evaluate(READ_STORES)

    chains_of: dict = {}
    for chain in data["chains"]:
        chains_of.setdefault(chain["channel"], []).append(chain)
    channels = sorted((c for c in data["channels"] if (c["team"] or "").startswith(PREFIX)), key=lambda c: c["team"])
    print(f"{len(channels)} class channels in the browser's store")
    for channel in channels:
        chains = chains_of.get(channel["id"], [])
        kinds = Counter(kind for chain in chains for kind in chain["kinds"])
        oldest = min((chain["started"] for chain in chains if chain["started"]), default=None)
        print(f"  {channel['team'][:44]:<44} #{str(channel['channel'])[:14]:<14} group {str(channel['group'])[:8]} "
              f"threads {len(chains):>3} oldest {oldest} folder {channel['folder']}")
        print(f"      {dict(kinds)}")


if __name__ == "__main__":
    main()
