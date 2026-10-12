"""
V9 and V18: download this semester's documents and see what they are made of.

Applies the rules the census suggested (no Student Work, no recordings, nothing last
changed before the semester, no photos, archives or media), downloads what is left,
converts Office files to PDF with LibreOffice, and reports page counts and how many
pages have no text layer. Needs pypdf:

    uv run --project sync --with pypdf python -m sync.probes.v09_files census.json folder
"""
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from sync.session import SHAREPOINT, browser, get, get_all, open_site

SEMESTER_START = "2026-07-01"
DOCUMENTS = (".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt")
OFFICE = (".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls")
SKIP_LIBRARIES = ("Student Work",)
# A page with fewer characters than this is a picture of a page, the same line the bot draws
SPARSE_PAGE_CHARS = 50


def extension(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else "(none)"


def download(census: list, folder: Path) -> list:
    manifest = []
    skipped: Counter = Counter()
    with browser() as context:
        open_site(context, SHAREPOINT)
        for site in census:
            if "error" in site:
                continue
            api = site["url"] + "/_api/v2.1"
            course = re.sub(r"[^\w.-]+", "_", site["title"])[:40]
            for drive in get_all(context, f"{api}/drives?$select=id,name"):
                if drive["name"] in SKIP_LIBRARIES:
                    continue
                items = get_all(context, f"{api}/drives/{drive['id']}/root/delta"
                                         "?$select=id,name,size,file,parentReference,lastModifiedDateTime,deleted")
                for item in items:
                    if "file" not in item or "deleted" in item:
                        continue
                    path = (item.get("parentReference", {}).get("path") or "").split("root:", 1)[-1]
                    kind = extension(item["name"])
                    if "/Recordings" in path:
                        continue
                    if kind not in DOCUMENTS:
                        skipped[f"type {kind}"] += 1
                        continue
                    if (item.get("lastModifiedDateTime") or "") < SEMESTER_START:
                        skipped["older than the semester"] += 1
                        continue
                    target = folder / course / f"{item['id']}{kind}"
                    if not target.exists():
                        reply = get(context, f"{api}/drives/{drive['id']}/items/{item['id']}/content", accept="*/*")
                        if not reply.ok:
                            skipped[f"download answered {reply.status}"] += 1
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(reply.body())
                    manifest.append({"course": site["title"], "library": drive["name"], "path": path,
                                     "name": item["name"], "kind": kind, "file": str(target),
                                     "bytes": target.stat().st_size})
    print(f"kept {len(manifest)} documents, {sum(m['bytes'] for m in manifest) / 1e6:.0f} MB. Left out: {dict(skipped)}")
    return manifest


def read_pdf(path: Path) -> dict:
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        # Reading every page of a long deck is slow, and the first 40 pages show what kind of file it is
        sample = [len((reader.pages[i].extract_text() or "").strip()) for i in range(min(pages, 40))]
        return {"pages": pages, "sampled": len(sample), "sparse": sum(1 for chars in sample if chars < SPARSE_PAGE_CHARS),
                "chars": sum(sample)}
    except Exception as error:
        return {"pages": 0, "sampled": 0, "sparse": 0, "chars": 0, "error": str(error)[:120]}


def convert(manifest: list, folder: Path) -> None:
    office = [m for m in manifest if m["kind"] in OFFICE]
    out = folder / "_converted"
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if office:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out)] + [m["file"] for m in office],
                       capture_output=True, timeout=900, check=False)
    took = time.monotonic() - started
    done = 0
    for entry in office:
        pdf = out / (Path(entry["file"]).stem + ".pdf")
        entry["converted"] = str(pdf) if pdf.exists() else None
        done += pdf.exists()
    print(f"LibreOffice converted {done} of {len(office)} Office files in {took:.0f}s")


def report(manifest: list) -> None:
    per_course: dict = {}
    long_decks, scanned, failed = [], [], []
    for entry in manifest:
        source = entry["file"] if entry["kind"] == ".pdf" else entry.get("converted")
        total = per_course.setdefault(entry["course"], {"files": 0, "pages": 0, "sparse": 0, "sampled": 0, "kinds": Counter()})
        total["files"] += 1
        total["kinds"][entry["kind"]] += 1
        if not source:
            continue
        found = read_pdf(Path(source))
        entry.update(found)
        if found.get("error") or not found["pages"]:
            failed.append(entry)
            continue
        total["pages"] += found["pages"]
        total["sparse"] += found["sparse"]
        total["sampled"] += found["sampled"]
        if found["pages"] > 150:
            long_decks.append(entry)
        if found["sampled"] and found["sparse"] / found["sampled"] > 0.5:
            scanned.append(entry)
    print(f"\n{'course':<34} {'files':>5} {'pages':>6} {'no text':>8}  types")
    for course, total in sorted(per_course.items()):
        share = total["sparse"] * 100 / (total["sampled"] or 1)
        print(f"{course[:34]:<34} {total['files']:>5} {total['pages']:>6} {share:>7.0f}%  {dict(total['kinds'])}")
    print(f"\ntotal pages {sum(t['pages'] for t in per_course.values())}")
    print(f"decks over 150 pages: {[(e['course'][7:13], e['pages']) for e in long_decks]}")
    print(f"mostly pictures of pages: {len(scanned)} files, {sum(e['pages'] for e in scanned)} pages, "
          f"by course {dict(Counter(e['course'][7:13] for e in scanned))}")
    print(f"could not be read: {[(e['course'][7:13], e['kind'], e.get('error', 'no pages')) for e in failed]}")
    office = [e for e in manifest if e["kind"] in OFFICE]
    print("converted Office files: " + ", ".join(
        f"{e['kind']} {e.get('pages', 0)}p/{e.get('chars', 0)}c" for e in office if e.get("converted")))


def main(census_file: Path, folder: Path) -> None:
    census = json.loads(census_file.read_text(encoding="utf-8"))
    manifest = download(census, folder)
    convert(manifest, folder)
    report(manifest)
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
