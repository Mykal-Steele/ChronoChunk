"""
V1, V2, V3 and V7: find every class site, then map how each one stores its files.

For each site it lists the libraries, walks them through the change feed, and counts
files by folder and type. Videos are checked for a transcript, and the site is checked
for a class notebook that somebody has written in. Reads only.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from sync.session import SHAREPOINT, browser, get, get_all, open_site

PREFIX = "1-2569"
VIDEO = (".mp4", ".mov", ".webm", ".mkv")


def find_sites(context) -> list:
    """Class sites the signed-in person can open, from SharePoint's own search."""
    query = quote(f"contentclass:STS_Site Title:{PREFIX}*")
    url = (f"{SHAREPOINT}/_api/search/query?querytext='{query}'"
           "&selectproperties='Title,Path,GroupId'&rowlimit=200&trimduplicates=false")
    reply = get(context, url, accept="application/json;odata=nometadata")
    rows = reply.json()["PrimaryQueryResult"]["RelevantResults"]["Table"]["Rows"]
    sites = []
    for row in rows:
        cells = {cell["Key"]: cell["Value"] for cell in row["Cells"]}
        sites.append({"title": cells.get("Title") or "", "url": cells.get("Path") or "", "group": cells.get("GroupId") or ""})
    return sorted(sites, key=lambda site: site["title"])


def extension(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else "(none)"


def census(context, site: dict) -> dict:
    api = site["url"] + "/_api/v2.1"
    result: dict = {"title": site["title"], "url": site["url"], "group": site["group"], "drives": [], "videos": []}
    for drive in get_all(context, f"{api}/drives?$select=id,name,webUrl"):
        items = get_all(context, f"{api}/drives/{drive['id']}/root/delta"
                                 "?$select=id,name,size,file,folder,parentReference,lastModifiedDateTime,deleted")
        files = [item for item in items if "file" in item and "deleted" not in item]
        by_type: Counter = Counter()
        by_folder: dict = defaultdict(lambda: [0, 0, ""])
        for item in files:
            by_type[extension(item["name"])] += 1
            # parentReference.path looks like /drives/<id>/root:/General/Recordings
            folder = (item.get("parentReference", {}).get("path") or "").split("root:", 1)[-1] or "/"
            top = "/".join(folder.split("/")[:3]) or "/"
            entry = by_folder[top]
            entry[0] += 1
            entry[1] += item.get("size") or 0
            entry[2] = max(entry[2], item.get("lastModifiedDateTime") or "")
            if item["name"].lower().endswith(VIDEO):
                result["videos"].append({"drive": drive["id"], "id": item["id"], "name": item["name"],
                                         "folder": folder, "size": item.get("size") or 0})
        result["drives"].append({
            "name": drive["name"], "files": len(files), "bytes": sum(item.get("size") or 0 for item in files),
            "types": dict(by_type.most_common()),
            "folders": {name: {"files": v[0], "bytes": v[1], "newest": v[2][:10]} for name, v in sorted(by_folder.items())},
        })
    for video in result["videos"]:
        reply = get(context, f"{api}/drives/{video['drive']}/items/{video['id']}/media/transcripts")
        transcripts = (reply.json().get("value") or []) if reply.ok else []
        video["transcripts"] = len(transcripts)
        video["transcript_bytes"] = sum(t.get("size") or 0 for t in transcripts)
    # A class notebook lives in the Site Assets library, which the drives listing leaves out
    notebook = get(context, f"{site['url']}/_api/web/GetFolderByServerRelativeUrl('SiteAssets')/Folders"
                            "?$select=Name,ItemCount,TimeLastModified", accept="application/json;odata=nometadata")
    result["site_assets"] = ([{"name": f["Name"], "items": f["ItemCount"], "changed": f["TimeLastModified"][:10]}
                              for f in notebook.json().get("value", [])] if notebook.ok else f"status {notebook.status}")
    return result


def main(out: Path) -> None:
    with browser() as context:
        open_site(context, SHAREPOINT)
        sites = find_sites(context)
        print(f"{len(sites)} class sites found")
        report = []
        for site in sites:
            try:
                report.append(census(context, site))
            except Exception as error:
                # One site that cannot be read must not hide the others
                print(f"!! {site['title']}: {error}")
                report.append({"title": site["title"], "url": site["url"], "error": str(error)})
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for site in report:
        print(f"\n== {site['title']}")
        if "error" in site:
            print(f"   could not be read: {site['error'][:200]}")
            continue
        for drive in site["drives"]:
            if not drive["files"]:
                continue
            print(f"   [{drive['name']}] {drive['files']} files, {drive['bytes'] / 1e6:.0f} MB, {drive['types']}")
            for name, folder in drive["folders"].items():
                print(f"      {name:<60} {folder['files']:>4} files {folder['bytes'] / 1e6:>8.1f} MB  newest {folder['newest']}")
        videos = site["videos"]
        without = [video["name"] for video in videos if not video.get("transcripts")]
        print(f"   videos: {len(videos)}, without a transcript: {len(without)} {without[:5]}")
        print(f"   site assets: {site['site_assets']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
