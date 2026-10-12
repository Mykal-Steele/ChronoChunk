"""
V3 and V17: download every transcript the census found, and see how many requests
SharePoint takes at once before it asks for a pause.

The downloads run inside a SharePoint page, so the browser sends them side by side.
Reports, per course, the hours of speech, the spoken languages, and how much of the
text the speech service itself was unsure about. With a folder as the third argument
the transcripts are saved there. Prints no transcript text.

    python -m sync.probes.v03_transcripts census.json [parallel,parallel,...] [folder]
"""
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from sync.session import SHAREPOINT, browser, open_site

# An entry the speech service scored below this is counted as unsure
UNSURE = 0.5

FETCH = """async ({jobs, parallel}) => {
  const results = new Array(jobs.length);
  const json = {accept: 'application/json'};
  let next = 0;
  const one = async (job) => {
    const started = performance.now();
    const out = {status: 0, paused: 0, text: null};
    const call = async (url) => {
      const reply = await fetch(url, {headers: json, credentials: 'include'});
      if (reply.status === 429 || reply.status === 503) out.paused += 1;
      out.status = reply.status;
      return reply.ok ? reply : null;
    };
    try {
      const listing = await call(job.api + '/media/transcripts');
      if (listing) {
        const found = (await listing.json()).value || [];
        const pick = found.find(t => t.isDefault) || found[0];
        if (pick) {
          const body = await call(job.api + '/media/transcripts/' + pick.id + '/streamContent?format=json');
          if (body) out.text = await body.text();
        }
      }
    } catch (error) { out.error = String(error); }
    out.ms = Math.round(performance.now() - started);
    return out;
  };
  const worker = async () => { while (next < jobs.length) { const i = next++; results[i] = await one(jobs[i]); } };
  await Promise.all(Array.from({length: parallel}, worker));
  return results;
}"""


def seconds(offset: str) -> float:
    hours, minutes, rest = offset.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def measure(text: str) -> dict:
    entries = json.loads(text).get("entries") or []
    words = unsure = thai = letters = 0
    languages: Counter = Counter()
    for entry in entries:
        body = entry.get("text") or ""
        count = len(body.split())
        words += count
        if float(entry.get("confidence") or 0) < UNSURE:
            unsure += count
        thai += len(re.findall(r"[฀-๿]", body))
        letters += len(re.findall(r"[A-Za-z฀-๿]", body))
        languages[(entry.get("spokenLanguageTag") or "?").lower()] += count
    return {"entries": len(entries), "words": words, "unsure": unsure, "thai": thai, "letters": letters,
            "languages": languages, "seconds": seconds(entries[-1]["endOffset"]) if entries else 0.0}


def main(census_file: Path, levels: list, folder: Path | None) -> None:
    census = json.loads(census_file.read_text(encoding="utf-8"))
    jobs = [{"course": site["title"], "name": video["name"],
             "api": f"{site['url']}/_api/v2.1/drives/{video['drive']}/items/{video['id']}"}
            for site in census for video in site.get("videos", []) if video.get("transcripts")]
    print(f"{len(jobs)} recordings with a transcript")

    with browser() as context:
        page = open_site(context, SHAREPOINT)
        results: list = []
        for parallel in levels:
            started = time.monotonic()
            results = page.evaluate(FETCH, {"jobs": [{"api": job["api"]} for job in jobs], "parallel": parallel})
            took = time.monotonic() - started
            good = [r for r in results if r["text"]]
            slowest = max((r["ms"] for r in results), default=0)
            print(f"  {parallel:>2} at once: {took:5.1f}s, {len(good)} of {len(jobs)} downloaded, "
                  f"{sum(len(r['text']) for r in good) / 1e6:.1f} MB, slowest {slowest} ms, "
                  f"asked to pause {sum(r['paused'] for r in results)} times, "
                  f"other answers {dict(Counter(r['status'] for r in results if not r['text']))}")

    per_course: dict = {}
    for job, result in zip(jobs, results, strict=True):
        if not result["text"]:
            continue
        if folder:
            course = re.sub(r"[^\w.-]+", "_", job["course"])[:40]
            target = folder / course / (re.sub(r"[^\w.-]+", "_", job["name"])[:120] + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result["text"], encoding="utf-8")
        found = measure(result["text"])
        total = per_course.setdefault(job["course"], {"recordings": 0, "words": 0, "unsure": 0, "thai": 0,
                                                      "letters": 0, "seconds": 0.0, "languages": Counter(), "empty": 0})
        total["recordings"] += 1
        total["empty"] += found["words"] < 200
        for key in ("words", "unsure", "thai", "letters", "seconds"):
            total[key] += found[key]
        total["languages"] += found["languages"]

    print(f"\n{'course':<34} {'rec':>3} {'hours':>5} {'words':>7} {'unsure':>6} {'thai':>5}  languages (share of words)")
    for course, total in sorted(per_course.items()):
        words = total["words"] or 1
        languages = ", ".join(f"{tag} {count * 100 // words}%" for tag, count in total["languages"].most_common(3))
        print(f"{course[:34]:<34} {total['recordings']:>3} {total['seconds'] / 3600:>5.1f} {total['words']:>7} "
              f"{total['unsure'] * 100 / words:>5.1f}% {total['thai'] * 100 / (total['letters'] or 1):>4.1f}%  {languages}"
              + (f"  ({total['empty']} nearly empty)" if total["empty"] else ""))


if __name__ == "__main__":
    main(Path(sys.argv[1]),
         [int(level) for level in sys.argv[2].split(",")] if len(sys.argv) > 2 else [8],
         Path(sys.argv[3]) if len(sys.argv) > 3 else None)
