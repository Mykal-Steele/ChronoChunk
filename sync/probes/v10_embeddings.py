"""
V10: does text-embedding-3-small find the right piece of a lecture for English, Thai
and mixed questions, and are 512 numbers per chunk enough?

Step one cuts real transcripts and slide pages into chunks and embeds them. Step two
runs a file of questions against them. A question passes when one of the top 6 chunks
contains its expected phrase. Needs the transcripts from v03 and the files from v09,
and AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY from the bot's .env:

    uv run --project sync --with openai --with numpy --with pypdf --with python-dotenv \\
        python -m sync.probes.v10_embeddings build transcripts/ files/manifest.json store/ CSC340 CSC345
    ... python -m sync.probes.v10_embeddings ask store/ questions.json
"""
import json
import os
import re
import sys
import time
from pathlib import Path

MODEL = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
# About this many seconds of speech per chunk, and each chunk starts with the last lines of the one before
CHUNK_SECONDS = 75
OVERLAP_ENTRIES = 2
SPARSE_PAGE_CHARS = 50
TOP = 6


def seconds(offset: str) -> float:
    hours, minutes, rest = offset.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def clock(total: float) -> str:
    return f"{int(total) // 3600}:{int(total) % 3600 // 60:02d}:{int(total) % 60:02d}"


def transcript_chunks(folder: Path, courses: list) -> list:
    chunks = []
    for path in sorted(folder.glob("*/*.json")):
        course = next((code for code in courses if code in path.parent.name), None)
        if not course:
            continue
        entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
        group: list = []
        cut = False
        for entry in entries:
            group.append(entry)
            if seconds(entry["endOffset"]) - seconds(group[0]["startOffset"]) >= CHUNK_SECONDS:
                chunks.append((course, path.stem, group))
                group = group[-OVERLAP_ENTRIES:]
                cut = True
        # What is left is new speech only when it holds more than the overlap carried over
        if len(group) > (OVERLAP_ENTRIES if cut else 0):
            chunks.append((course, path.stem, group))
    return [{"course": course, "kind": "transcript", "source": name, "where": clock(seconds(group[0]["startOffset"])),
             "text": " ".join(entry.get("text") or "" for entry in group)} for course, name, group in chunks]


def slide_chunks(manifest_file: Path, courses: list) -> list:
    from pypdf import PdfReader
    chunks = []
    for entry in json.loads(manifest_file.read_text(encoding="utf-8")):
        course = next((code for code in courses if code in entry["course"]), None)
        source = entry["file"] if entry["kind"] == ".pdf" else entry.get("converted")
        if not course or not source:
            continue
        try:
            pages = PdfReader(source).pages
            for number, page in enumerate(pages, 1):
                text = " ".join((page.extract_text() or "").split())
                if len(text) >= SPARSE_PAGE_CHARS:
                    chunks.append({"course": course, "kind": "slide", "source": entry["name"], "where": f"page {number}",
                                   "text": text[:3000]})
        except Exception as error:
            print(f"!! {entry['name']}: {str(error)[:100]}")
    return chunks


def header(chunk: dict) -> str:
    """What is embedded: the chunk with a line that says where it is from."""
    return f"{chunk['course']} | {chunk['kind']} | {chunk['source']} | {chunk['where']}\n{chunk['text']}"


def embed(texts: list) -> list:
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv()
    client = OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.environ["AZURE_OPENAI_KEY"], max_retries=8)
    vectors, tokens = [], 0
    for start in range(0, len(texts), 100):
        reply = client.embeddings.create(model=MODEL, input=texts[start:start + 100])
        vectors.extend(item.embedding for item in reply.data)
        tokens += reply.usage.total_tokens
    embed.tokens = getattr(embed, "tokens", 0) + tokens
    return vectors


def build(transcripts: Path, manifest: Path, store: Path, courses: list) -> None:
    import numpy
    chunks = transcript_chunks(transcripts, courses) + slide_chunks(manifest, courses)
    print(f"{sum(c['kind'] == 'transcript' for c in chunks)} transcript chunks, {sum(c['kind'] == 'slide' for c in chunks)} slide pages")
    started = time.monotonic()
    vectors = numpy.array(embed([header(chunk) for chunk in chunks]), dtype="float32")
    store.mkdir(parents=True, exist_ok=True)
    numpy.save(store / "vectors.npy", vectors)
    (store / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    print(f"embedded {len(chunks)} chunks, {embed.tokens} tokens, {time.monotonic() - started:.0f}s, "
          f"about ${embed.tokens * 0.02 / 1e6:.4f} at $0.02 per million")


def shorten(vectors, size: int):
    """The first numbers of each vector, scaled back to length one. The model is built to allow this."""
    import numpy
    cut = vectors[:, :size]
    return cut / numpy.linalg.norm(cut, axis=1, keepdims=True)


def ask(store: Path, questions_file: Path) -> None:
    import numpy
    chunks = json.loads((store / "chunks.json").read_text(encoding="utf-8"))
    vectors = numpy.load(store / "vectors.npy")
    questions = json.loads(questions_file.read_text(encoding="utf-8"))
    started = time.monotonic()
    asked = numpy.array(embed([q["question"] for q in questions]), dtype="float32")
    per_question = (time.monotonic() - started) / len(questions)
    for size in (512, 1536):
        chunk_side, question_side = shorten(vectors, size), shorten(asked, size)
        passed: dict = {}
        misses = []
        for index, question in enumerate(questions):
            order = numpy.argsort(-(chunk_side @ question_side[index]))
            if question.get("absent"):
                continue
            wanted = [phrase.lower() for phrase in question["expect"]]
            in_course = [i for i in order if not question.get("course") or chunks[i]["course"] == question["course"]]
            rank = next((place for place, i in enumerate(in_course[:50], 1)
                         if any(phrase in chunks[i]["text"].lower() for phrase in wanted)), None)
            tally = passed.setdefault(question["language"], [0, 0])
            tally[0] += bool(rank and rank <= TOP)
            tally[1] += 1
            if not rank or rank > TOP:
                misses.append(f"{question['language']}: {question['question'][:60]} (rank {rank})")
        print(f"{size} numbers: " + ", ".join(f"{language} {good}/{total}" for language, (good, total) in passed.items()))
        for miss in misses:
            print(f"     missed {miss}")
    print(f"embedding a question took {per_question * 1000:.0f} ms each in one batch, {embed.tokens} tokens in all")


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), [re.sub(r"\W", "", code) for code in sys.argv[5:]])
    else:
        ask(Path(sys.argv[2]), Path(sys.argv[3]))
