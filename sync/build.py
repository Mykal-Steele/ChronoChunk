"""
One sync run, start to finish: collect what the class sites hold, read it, and write a
new course file.

The browser is only open while collecting. Reading, translating and embedding come
after, and all of it is cached, so a run can be stopped and started again. The new
file is written beside the old one, checked, and only then swapped in. A run that
fails leaves the old file as it was.
"""
import logging
import os
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.course_store import DIMENSIONS, CourseStore
from sync.cache import Cache
from sync.material import (
    CHUNK_MIN_CHARS,
    VIDEO,
    Course,
    course_from_title,
    embedded_text,
    extension,
    has_thai,
    local_day,
    recording_title,
    transcript_chunks,
    wanted,
)
from sync.models import Models
from sync.reading import READER_VERSION, Page, ReadError, Reading, read_document
from sync.settings import COURSE_PREFIX, EMBEDDING_MODEL, SEMESTER_START

logger = logging.getLogger(__name__)

# A new file with fewer than this share of a course's old chunks is not swapped in
# without --force. Material rarely disappears, so a big drop means the run went wrong.
KEEP_SHARE = 0.7
# Class Materials is read first, so a file that is also attached to an assignment is filed under its lecture
LIBRARY_ORDER = {"Class Materials": 0, "Documents": 1}


@dataclass
class CourseMaterial:
    """What the sites of one course hold, after downloading."""
    course: Course
    sites: List[dict] = field(default_factory=list)
    documents: List[dict] = field(default_factory=list)
    # (the recording's file, its transcript entries)
    transcripts: List[Tuple[dict, List[dict]]] = field(default_factory=list)
    recordings_without_transcript: List[str] = field(default_factory=list)
    left_out: Counter = field(default_factory=Counter)
    failed: List[str] = field(default_factory=list)


@dataclass
class Report:
    lines: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.lines.append(line)
        logger.info(line)

    def problem(self, line: str) -> None:
        self.problems.append(line)
        logger.warning(line)


class BadRun(RuntimeError):
    """The new course file failed its checks. The old one stays in place."""


def collect(only: Sequence[str], cache: Cache, report: Report, headless: bool = True) -> List[CourseMaterial]:
    """Open the saved sign-in, list every class site, and download what changed."""
    from sync import sharepoint
    from sync.session import SHAREPOINT, browser, open_site

    started = time.monotonic()
    by_code: Dict[str, CourseMaterial] = {}
    with browser(headless=headless) as context:
        page = open_site(context, SHAREPOINT)
        for site in sharepoint.find_sites(context, COURSE_PREFIX):
            course = course_from_title(site["title"], COURSE_PREFIX)
            if course is None:
                report.problem(f"{site['title']}: no course code in the title, left out")
                continue
            if only and course.code not in only:
                continue
            material = by_code.setdefault(course.code, CourseMaterial(course))
            material.sites.append(site)
            try:
                files = sharepoint.list_files(context, site)
            except Exception as error:
                # One site that cannot be read must not stop the others
                material.failed.append(f"site {site['title']}: {str(error)[:200]}")
                continue
            for file in files:
                if extension(file["name"]) in VIDEO:
                    continue
                keep, why = wanted(file["library"], file["folder"], file["name"], file["modified"], SEMESTER_START)
                if keep:
                    material.documents.append(file)
                else:
                    material.left_out[why] += 1
            try:
                transcripts = sharepoint.fetch_transcripts(page, files, cache)
            except Exception as error:
                material.failed.append(f"transcripts of {site['title']}: {str(error)[:200]}")
                transcripts = {}
            for file in files:
                if extension(file["name"]) in VIDEO and "/Recordings" in file["folder"]:
                    if file["id"] in transcripts:
                        material.transcripts.append((file, transcripts[file["id"]]))
                    else:
                        material.recordings_without_transcript.append(file["name"])

        every = [file for material in by_code.values() for file in material.documents]
        fresh, failed = sharepoint.download(context, every, cache)
        for material in by_code.values():
            for file in [file for file in material.documents if "error" in file]:
                material.failed.append(f"download {file['name']}: {file['error']}")
            material.documents = [file for file in material.documents if "path" in file]
    report.say(f"Collected {len(by_code)} courses in {time.monotonic() - started:.0f}s: {len(every)} documents "
               f"({fresh} downloaded, {len(failed)} failed), {sum(len(m.transcripts) for m in by_code.values())} transcripts")
    return [by_code[code] for code in sorted(by_code)]


def _read(file: dict, models: Models, cache: Cache) -> Reading:
    """One document's pages, from the cache when this content was read before."""
    key = f"{READER_VERSION}:{file['sha']}"
    cached = cache.get("reading", key)
    if cached is not None:
        return Reading([Page(**page) for page in cached["pages"]], cached["described"], 0, cached["note"])
    reading = read_document(Path(file["path"]), extension(file["name"]), file["sha"], models, cache)
    # A reading with failed requests is not kept, so the next run asks for those descriptions again
    if not reading.failed_requests:
        cache.put("reading", key, {"pages": [vars(page) for page in reading.pages], "described": reading.described,
                                   "note": reading.note})
    return reading


def _add_course(store: CourseStore, material: CourseMaterial, models: Models, cache: Cache, report: Report) -> None:
    course = material.course
    group = next((site["group"] for site in material.sites if site["group"]), "")
    store.add_course(course.code, course.name, course.aliases, group_id=group, site_url=material.sites[0]["url"])

    # (kind, title, section, url, date, hash, [(locator, text, original)])
    sources: List[tuple] = []
    seen_content = set()
    pages = described = duplicates = 0
    ordered = sorted(material.documents, key=lambda file: (LIBRARY_ORDER.get(file["library"], 2), file["folder"], file["name"]))
    for file in ordered:
        if file["sha"] in seen_content:
            duplicates += 1
            continue
        seen_content.add(file["sha"])
        try:
            reading = _read(file, models, cache)
        except ReadError as error:
            material.failed.append(f"read {file['name']}: {error}")
            continue
        if reading.failed_requests:
            material.failed.append(f"{file['name']}: {reading.failed_requests} description requests failed, text only for those pages")
        if reading.note:
            report.say(f"  {file['name']}: {reading.note}")
        chunks = [(f"page {page.number}", page.text, page.original) for page in reading.pages if len(page.text) >= CHUNK_MIN_CHARS]
        pages += len(chunks)
        described += reading.described
        if chunks:
            sources.append(("slide", file["name"], file["folder"].strip("/"), file["url"], local_day(file["modified"]),
                            file["sha"], chunks))

    spoken = 0
    for file, entries in sorted(material.transcripts, key=lambda pair: pair[0]["created"]):
        cut = transcript_chunks(entries)
        thai = [text for _, text in cut if has_thai(text)]
        english = dict(zip(thai, models.translate(thai), strict=True))
        chunks = [(start, english.get(text, text), text if text in english else None) for start, text in cut
                  if len(text) >= CHUNK_MIN_CHARS]
        spoken += len(chunks)
        if chunks:
            sources.append(("transcript", recording_title(file["name"]), "", file["url"], local_day(file["created"]), "", chunks))

    texts = [embedded_text(course, kind, title, section, locator, text)
             for kind, title, section, _, _, _, chunks in sources for locator, text, _ in chunks]
    vectors = iter(models.embed(texts))
    for kind, title, section, url, date, content_hash, chunks in sources:
        source_id = store.add_source(course.code, kind, title, section=section, url=url, date=date, content_hash=content_hash)
        for position, (locator, text, original) in enumerate(chunks):
            store.add_chunk(source_id, position, locator, text, next(vectors), original=original)
    store.save()

    left_out = ", ".join(f"{count} {why}" for why, count in material.left_out.most_common()) or "nothing"
    report.say(f"{course.code} {course.name}: {len(seen_content)} documents, {pages} pages ({described} described), "
               f"{len(material.transcripts)} transcripts, {spoken} transcript chunks")
    report.say(f"  left out: {left_out}; {duplicates} copies of a file already read")
    for name in material.recordings_without_transcript:
        report.say(f"  no transcript: {name}")
    for failure in material.failed:
        report.problem(f"  {course.code} failed: {failure}")


def _chunk_counts(path: Path) -> Dict[str, int]:
    store = CourseStore.open(str(path))
    try:
        return store.chunk_counts()
    finally:
        store.close()


def check(new: Path, old: Optional[Path]) -> None:
    """Raise BadRun when the new file is empty, unreadable, or lost much of what the old one had."""
    try:
        now = _chunk_counts(new)
    except Exception as error:
        raise BadRun(f"the new file cannot be read back: {error}") from error
    if not now:
        raise BadRun("the new file has no chunks")
    if old is None or not old.exists():
        return
    try:
        before = _chunk_counts(old)
    except Exception:
        # An old file this version cannot read is no measure of anything
        return
    for code, count in before.items():
        if now.get(code, 0) < count * KEEP_SHARE:
            raise BadRun(f"{code} had {count} chunks and would now have {now.get(code, 0)}")


def build(materials: Sequence[CourseMaterial], models: Models, cache: Cache, out: Path, report: Report,
          force: bool = False) -> None:
    """Write a new course file from the collected material and swap it in for the old one."""
    started = time.monotonic()
    new = out.with_name(out.name + ".new")
    new.unlink(missing_ok=True)
    store = CourseStore.create(str(new))
    try:
        for material in materials:
            _add_course(store, material, models, cache, report)
        store.set_meta("synced_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        store.set_meta("embedding_model", EMBEDDING_MODEL)
        store.set_meta("embedding_dimensions", str(DIMENSIONS))
        store.save()
    finally:
        store.close()

    try:
        check(new, None if force else out)
    except BadRun:
        new.unlink(missing_ok=True)
        raise
    if out.exists():
        # Copied, not moved: the old file stays in place until the one step that replaces it
        shutil.copy2(out, out.with_name(out.name + ".previous"))
    os.replace(new, out)
    report.say(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.monotonic() - started:.0f}s. "
               f"Model calls: {models.calls}, about ${models.spent:.4f}")
