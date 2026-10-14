"""
Reads the class sites through the saved sign-in: which sites exist, which files each
one holds, the transcripts of its recordings, and the documents themselves. Nothing
is changed on SharePoint. A file or transcript whose content tag is the one the cache
already has is not fetched again.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

from sync.cache import Cache, file_sha
from sync.material import SKIPPED_LIBRARIES, VIDEO, extension
from sync.session import get, get_all, get_json
from sync.settings import DOWNLOADS_AT_ONCE, FILES_DIR, REQUESTS_AT_ONCE, SHAREPOINT

logger = logging.getLogger(__name__)

# A download link is good for a short while, so links are asked for a few at a time
LINKS_PER_ROUND = 12
DOWNLOAD_TRIES = 3
DOWNLOAD_SECONDS = 600

# Runs inside a SharePoint page, where the browser sends many requests side by side
# with the sign-in it already has. A 429 or 503 is retried after the pause it asks for.
FETCH_ALL = """async ({urls, parallel}) => {
  const results = new Array(urls.length);
  let next = 0;
  const one = async (url) => {
    for (let attempt = 0; ; attempt++) {
      try {
        const reply = await fetch(url, {headers: {accept: 'application/json'}, credentials: 'include'});
        if ((reply.status === 429 || reply.status === 503) && attempt < 4) {
          const pause = Number(reply.headers.get('retry-after')) || 2 ** (attempt + 1);
          await new Promise(done => setTimeout(done, Math.min(pause, 120) * 1000));
          continue;
        }
        return {status: reply.status, text: reply.ok ? await reply.text() : ''};
      } catch (error) {
        if (attempt >= 2) return {status: 0, text: '', error: String(error)};
      }
    }
  };
  const worker = async () => { while (next < urls.length) { const i = next++; results[i] = await one(urls[i]); } };
  await Promise.all(Array.from({length: parallel}, worker));
  return results;
}"""


def find_sites(context: BrowserContext, prefix: str) -> List[dict]:
    """Class sites the signed-in person can open, from SharePoint's own search."""
    query = quote(f"contentclass:STS_Site Title:{prefix}*")
    url = (f"{SHAREPOINT}/_api/search/query?querytext='{query}'"
           "&selectproperties='Title,Path,GroupId'&rowlimit=200&trimduplicates=false")
    reply = get(context, url, accept="application/json;odata=nometadata")
    if not reply.ok:
        raise RuntimeError(f"the site search answered {reply.status}")
    sites = []
    for row in reply.json()["PrimaryQueryResult"]["RelevantResults"]["Table"]["Rows"]:
        cells = {cell["Key"]: cell["Value"] for cell in row["Cells"]}
        # The search matches the prefix anywhere in a word, so the title is checked again
        if (cells.get("Title") or "").startswith(prefix):
            sites.append({"title": cells["Title"], "url": cells.get("Path") or "", "group": cells.get("GroupId") or ""})
    return sorted(sites, key=lambda site: site["title"])


def list_files(context: BrowserContext, site: dict) -> List[dict]:
    """Every file in the site's libraries, except the student's own work."""
    api = site["url"] + "/_api/v2.1"
    files = []
    for drive in get_all(context, f"{api}/drives?$select=id,name"):
        if drive["name"] in SKIPPED_LIBRARIES:
            continue
        items = get_all(context, f"{api}/drives/{drive['id']}/root/delta"
                                 "?$select=id,name,size,file,parentReference,lastModifiedDateTime,createdDateTime,cTag,webUrl,deleted")
        for item in items:
            if "file" not in item or "deleted" in item:
                continue
            # parentReference.path looks like /drives/<id>/root:/General/Recordings
            folder = (item.get("parentReference", {}).get("path") or "").split("root:", 1)[-1]
            files.append({"id": item["id"], "api": f"{api}/drives/{drive['id']}/items/{item['id']}", "library": drive["name"],
                          "folder": folder, "name": item["name"], "size": item.get("size") or 0,
                          "modified": item.get("lastModifiedDateTime") or "", "created": item.get("createdDateTime") or "",
                          "ctag": item.get("cTag") or "", "url": item.get("webUrl") or ""})
    return files


def fetch_transcripts(page: Page, files: List[dict], cache: Cache) -> Dict[str, List[dict]]:
    """
    The transcript of every recording that has one, as {file id: entries}. An entry has
    text, startOffset and endOffset. Recordings without a transcript are left out.
    """
    videos = [file for file in files if extension(file["name"]) in VIDEO]
    listings = page.evaluate(FETCH_ALL, {"urls": [video["api"] + "/media/transcripts" for video in videos],
                                         "parallel": REQUESTS_AT_ONCE})
    result: Dict[str, List[dict]] = {}
    wanted = []
    for video, listing in zip(videos, listings, strict=True):
        if listing["status"] != 200:
            stale = cache.get("transcript", video["id"])
            logger.warning(f"{video['name']}: the transcript list answered {listing['status']}"
                           + (", the last copy is used" if stale else ""))
            if stale:
                result[video["id"]] = stale["entries"]
            continue
        found = json.loads(listing["text"]).get("value") or []
        pick = next((transcript for transcript in found if transcript.get("isDefault")), found[0] if found else None)
        if pick is None:
            continue
        cached = cache.get("transcript", video["id"])
        if cached and cached["ctag"] == pick.get("cTag"):
            result[video["id"]] = cached["entries"]
        else:
            wanted.append((video, pick))
    bodies = page.evaluate(FETCH_ALL, {
        "urls": [f"{video['api']}/media/transcripts/{pick['id']}/streamContent?format=json" for video, pick in wanted],
        "parallel": REQUESTS_AT_ONCE}) if wanted else []
    for (video, pick), body in zip(wanted, bodies, strict=True):
        if body["status"] != 200:
            stale = cache.get("transcript", video["id"])
            logger.warning(f"{video['name']}: the transcript answered {body['status']}"
                           + (", the last copy is used" if stale else ""))
            if stale:
                result[video["id"]] = stale["entries"]
            continue
        entries = [{"text": entry.get("text") or "", "startOffset": entry["startOffset"], "endOffset": entry["endOffset"]}
                   for entry in json.loads(body["text"]).get("entries") or []]
        cache.put("transcript", video["id"], {"ctag": pick.get("cTag"), "entries": entries})
        result[video["id"]] = entries
    return result


def _save(link: str, target: Path) -> None:
    """
    Download to a .part file and rename it when it is whole, so a broken download never
    replaces a good copy. The length is checked against what the server said it would
    send. The size in the file listing is not used: SharePoint adds to Office files as
    it serves them, so that number can differ from a complete download.
    """
    part = target.with_name(target.name + ".part")
    with urllib.request.urlopen(link, timeout=DOWNLOAD_SECONDS) as reply, open(part, "wb") as out:
        promised = reply.headers.get("Content-Length")
        while block := reply.read(1 << 20):
            out.write(block)
    got = part.stat().st_size
    if got == 0 or (promised and got != int(promised)):
        part.unlink()
        raise OSError(f"got {got} bytes, expected {promised or 'more than none'}")
    part.replace(target)


def download(context: BrowserContext, files: List[dict], cache: Cache,
             progress: Optional[Callable[[int, int], None]] = None) -> Tuple[int, List[dict]]:
    """
    Make sure each file is on disk, and set its "path" and "sha". Returns how many were
    fetched and the files that could not be, each with an "error". A file whose content
    tag has not changed since it was last downloaded is left alone. A file that changed
    and cannot be fetched keeps the copy from last time, so one bad download does not
    take a deck out of the course file.
    """
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    needed = []
    stale: Dict[str, dict] = {}
    for file in files:
        known = cache.get("download", file["id"])
        if known and Path(known["path"]).exists():
            if known["ctag"] == file["ctag"]:
                file["path"], file["sha"] = known["path"], known["sha"]
                continue
            stale[file["id"]] = known
        needed.append(file)

    failed = []

    def fetch(job: tuple) -> None:
        file, link = job
        target = FILES_DIR / (file["id"] + extension(file["name"]))
        for attempt in range(DOWNLOAD_TRIES):
            try:
                _save(link, target)
                file["path"], file["sha"] = str(target), file_sha(target)
                cache.put("download", file["id"], {"ctag": file["ctag"], "path": file["path"], "sha": file["sha"]})
                return
            except Exception as error:
                file["error"] = repr(error)[:200]
                time.sleep(2 * (attempt + 1))
        failed.append(file)

    done = 0
    for start in range(0, len(needed), LINKS_PER_ROUND):
        jobs = []
        for file in needed[start:start + LINKS_PER_ROUND]:
            # The content address answers with a redirect to a link that needs no sign-in
            reply = context.request.get(file["api"] + "/content", max_redirects=0)
            link = reply.headers.get("location")
            if reply.status in (301, 302) and link:
                jobs.append((file, link))
            else:
                file["error"] = f"no download link, status {reply.status}"
                failed.append(file)
        with ThreadPoolExecutor(DOWNLOADS_AT_ONCE) as pool:
            list(pool.map(fetch, jobs))
        done += len(jobs)
        if progress:
            progress(done, len(needed))
    for file in failed:
        if file["id"] in stale:
            file["path"], file["sha"] = stale[file["id"]]["path"], stale[file["id"]]["sha"]
            file["error"] += ", the last copy is used"
    for file in files:
        if file not in failed:
            file.pop("error", None)
    return len(needed) - len(failed), failed


def fetch_pictures(context: BrowserContext, facts: List[dict], cache: Cache) -> List[str]:
    """
    Download the pictures set into each assignment's instructions, and note them on the
    assignment as "pictures": where the file is on disk, its hash and its link. A picture
    that cannot be fetched is left out, the assignment keeps its text, and what went
    wrong is returned for the run's report.
    """
    from sync.assignments import picture_refs

    wanted: List[Tuple[dict, dict]] = []
    problems: List[str] = []
    for fact in facts:
        for drive, item in picture_refs(fact.get("instructions")):
            api = f"{SHAREPOINT}/_api/v2.1/drives/{drive}/items/{item}"
            try:
                found = get_json(context, api + "?$select=id,name,cTag,webUrl")
            except Exception as error:
                problems.append(f"a picture in assignment {fact.get('displayName')!r} could not be found: {str(error)[:150]}")
                continue
            wanted.append((fact, {"id": found.get("id") or item, "api": api, "name": found.get("name") or "picture.png",
                                  "ctag": found.get("cTag") or "", "url": found.get("webUrl") or ""}))
    download(context, [file for _, file in wanted], cache)
    for fact, file in wanted:
        if "path" in file:
            fact.setdefault("pictures", []).append({"path": file["path"], "sha": file["sha"], "url": file["url"]})
        else:
            problems.append(f"a picture in assignment {fact.get('displayName')!r} could not be downloaded: "
                            f"{str(file.get('error') or 'no reason given')[:150]}")
    return problems
