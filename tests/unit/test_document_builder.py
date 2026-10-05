"""Unit tests for document_builder: Markdown to PDF, DOCX and diagrams. Needs pandoc and WeasyPrint."""
import base64
import io
import json
import os
import zipfile
import zlib
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from PIL import Image
from pypdf import PdfReader

from src import document_builder
from src.document_builder import (
    DiagramSyntaxError, DocumentError, build_diagram, build_document, clean_document_text, fix_flowchart_labels,
    fix_sequence_names, render_mermaid, slugify, split_mermaid,
)

def _png() -> bytes:
    """A small valid PNG, enough for pandoc and WeasyPrint to embed."""
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, "PNG")
    return buffer.getvalue()


PNG = _png()

DOC = """# Quarterly report

Revenue rose 12 percent.

| Region | Revenue |
|---|---|
| North | 40 |
| South | 60 |

1. Review the numbers.
2. Send the report.
"""


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def docx_xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")


@pytest.fixture
def fake_renderer(monkeypatch):
    renderer = AsyncMock(return_value=PNG)
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    return renderer


# ── filenames and text cleanup ────────────────────────────────────────────────

def test_slugify_makes_a_safe_filename():
    assert slugify("Q3 Report: Sales & Growth!") == "q3-report-sales-growth"
    assert slugify("../../etc/passwd") == "etc-passwd"


def test_slugify_falls_back_when_nothing_is_left():
    assert slugify("สวัสดี") == "document"
    assert slugify("", "diagram") == "diagram"


def test_slugify_falls_back_when_only_scraps_of_ascii_are_left():
    # a Thai title with one digit in it must not become "2.pdf"
    assert slugify("จดหมายลาป่วย 2 วัน") == "document"
    assert slugify("CV") == "cv"
    assert slugify("FAQ 2026") == "faq-2026"


def test_normalize_adds_the_blank_line_a_table_needs():
    from src.document_builder import normalize_markdown
    fixed = normalize_markdown("Timeline\n| Week | Work |\n| --- | --- |\n| 1 | Design |")
    assert fixed == "Timeline\n\n| Week | Work |\n| --- | --- |\n| 1 | Design |"


def test_normalize_leaves_good_markdown_and_code_alone():
    from src.document_builder import normalize_markdown
    good = "## Timeline\n\n| Week | Work |\n| --- | --- |\n\n```\necho hi\n| not a table\n```"
    assert normalize_markdown(good) == good


async def test_table_right_after_a_text_line_still_renders_as_a_table():
    built = await build_document("docx", "Plan", "# Plan\n\nTimeline\n| Week | Work |\n| --- | --- |\n| 1 | Design |")
    assert docx_xml(built.data).count("<w:tbl>") == 1


def test_clean_turns_dashes_between_words_into_commas():
    assert clean_document_text("Costs fell — a lot.") == "Costs fell, a lot."


def test_clean_keeps_number_ranges_as_hyphens():
    assert clean_document_text("Open 9:00–17:00, weeks 1 – 3.") == "Open 9:00-17:00, weeks 1-3."


def test_clean_removes_a_dash_that_opens_a_line_or_list_item():
    assert clean_document_text("- — first\n— second") == "- first\nsecond"


def test_clean_straightens_curly_quotes():
    assert clean_document_text("“Quoted” and ‘single’") == "\"Quoted\" and 'single'"


def test_clean_leaves_code_blocks_alone():
    text = "Text — here\n```\ncode — stays\n```"
    assert clean_document_text(text) == "Text, here\n```\ncode — stays\n```"


# ── building documents ────────────────────────────────────────────────────────

async def test_markdown_file_is_returned_as_written():
    built = await build_document("md", "Quarterly report", DOC)
    assert built.filename == "quarterly-report.md"
    assert built.data.decode("utf-8").startswith("# Quarterly report")


async def test_pdf_contains_the_text_and_table():
    built = await build_document("pdf", "Quarterly report", DOC)
    assert built.filename == "quarterly-report.pdf"
    assert built.data.startswith(b"%PDF")
    text = pdf_text(built.data)
    assert "Quarterly report" in text
    assert "Revenue rose 12 percent." in text
    assert "North" in text and "60" in text


async def test_docx_contains_the_text_and_a_real_table():
    built = await build_document("docx", "Quarterly report", DOC)
    assert built.filename == "quarterly-report.docx"
    xml = docx_xml(built.data)
    assert "Revenue rose 12 percent." in xml
    assert xml.count("<w:tbl>") == 1


async def test_format_is_case_and_dot_tolerant():
    built = await build_document(".PDF", "x", "# Title\n\nBody.")
    assert built.filename == "x.pdf"


async def test_single_newlines_stay_as_line_breaks():
    built = await build_document("docx", "Letter", "# Letter\n\nMykal Stele\nBangkok\nThailand")
    assert docx_xml(built.data).count("<w:br") == 2


async def test_unsupported_format_is_refused():
    with pytest.raises(DocumentError, match="unsupported format"):
        await build_document("xlsx", "x", "hi")


async def test_empty_document_is_refused():
    with pytest.raises(DocumentError, match="empty"):
        await build_document("pdf", "x", "   ")


async def test_oversized_document_is_refused(monkeypatch):
    monkeypatch.setattr(document_builder, "MAX_DOCUMENT_CHARS", 50)
    with pytest.raises(DocumentError, match="too long"):
        await build_document("pdf", "x", "word " * 30)


# ── diagrams inside documents ─────────────────────────────────────────────────

MERMAID_DOC = "# Plan\n\nThe flow:\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\nDone.\n"


async def test_mermaid_block_becomes_an_image_in_a_docx(fake_renderer):
    built = await build_document("docx", "Plan", MERMAID_DOC)
    names = zipfile.ZipFile(io.BytesIO(built.data)).namelist()
    assert any(name.startswith("word/media/") for name in names)
    assert "flowchart TD" not in docx_xml(built.data)
    fake_renderer.assert_awaited_once()
    assert fake_renderer.await_args[0][0].strip() == "flowchart TD\n  A --> B"


async def test_mermaid_block_becomes_an_image_in_a_pdf(fake_renderer):
    built = await build_document("pdf", "Plan", MERMAID_DOC)
    reader = PdfReader(io.BytesIO(built.data))
    assert len(reader.pages[0].images) == 1
    assert "flowchart TD" not in pdf_text(built.data)


async def test_diagram_that_fails_to_render_stays_as_code(monkeypatch):
    monkeypatch.setattr(document_builder, "render_mermaid", AsyncMock(side_effect=DocumentError("renderer down")))
    built = await build_document("pdf", "Plan", MERMAID_DOC)
    assert "flowchart TD" in pdf_text(built.data)


async def test_only_the_first_diagrams_are_rendered(fake_renderer, monkeypatch):
    monkeypatch.setattr(document_builder, "MAX_DIAGRAMS_PER_DOCUMENT", 2)
    doc = "# Many\n\n" + "\n\n".join(f"```mermaid\nflowchart TD\n  A{i} --> B{i}\n```" for i in range(4))
    await build_document("docx", "Many", doc)
    assert fake_renderer.await_count == 2


# ── hostile content ───────────────────────────────────────────────────────────

HOSTILE = """# Notes

![secret](file:///proc/self/environ)
![remote](http://169.254.169.254/latest/meta-data/)
<img src="file:///etc/passwd"><link rel="attachment" href="file:///proc/self/environ">

Safe line.
"""


async def test_pdf_never_embeds_local_files_or_the_environment(monkeypatch):
    monkeypatch.setenv("CANARY_SECRET", "canary-value-9f3a")
    built = await build_document("pdf", "Notes", HOSTILE)
    reader = PdfReader(io.BytesIO(built.data))
    assert not reader.attachments
    assert all(len(page.images) == 0 for page in reader.pages)
    assert b"canary-value-9f3a" not in built.data
    text = pdf_text(built.data)
    assert "Safe line." in text and "root:" not in text


async def test_docx_never_embeds_local_or_remote_images(monkeypatch):
    monkeypatch.setenv("CANARY_SECRET", "canary-value-9f3a")
    built = await build_document("docx", "Notes", HOSTILE)
    archive = zipfile.ZipFile(io.BytesIO(built.data))
    assert not [name for name in archive.namelist() if name.startswith("word/media/")]
    assert all(b"canary-value-9f3a" not in archive.read(name) for name in archive.namelist())
    assert "Safe line." in docx_xml(built.data)


async def test_pdf_fetcher_blocks_everything_outside_the_work_folder(tmp_path):
    inside = tmp_path / "diagram-1.png"
    inside.write_bytes(PNG)
    fetcher = document_builder._workdir_fetcher(str(tmp_path))
    assert fetcher.fetch(inside.as_uri()) is not None
    for url in ("file:///etc/passwd", "http://169.254.169.254/", f"{tmp_path.as_uri()}/../outside.png"):
        with pytest.raises(ValueError):
            fetcher.fetch(url)


# ── the mermaid renderer ──────────────────────────────────────────────────────

def _fake_session(monkeypatch, status=200, body=PNG, error=None):
    """Stand in for aiohttp so no real request is made. Records the requested URL."""
    seen = {}
    response = MagicMock()
    response.status = status
    response.read = AsyncMock(return_value=body)
    response_cm = MagicMock()
    response_cm.__aenter__ = AsyncMock(return_value=response)
    response_cm.__aexit__ = AsyncMock(return_value=False)

    def get(url):
        seen["url"] = url
        if error:
            raise error
        return response_cm

    session = MagicMock()
    session.get = get
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(document_builder.aiohttp, "ClientSession", MagicMock(return_value=session_cm))
    return seen


async def test_render_returns_the_png(monkeypatch):
    seen = _fake_session(monkeypatch)
    assert await render_mermaid("flowchart TD\n  A --> B") == PNG
    assert seen["url"].startswith("https://mermaid.ink/img/pako:")
    assert "type=png" in seen["url"]


async def test_render_reports_a_syntax_error_with_the_parser_message(monkeypatch):
    _fake_session(monkeypatch, status=400, body=b"Parse error on line 2: unexpected token")
    with pytest.raises(DocumentError, match="syntax error: Parse error on line 2"):
        await render_mermaid("flowchart TD\n  A[oops")


async def test_render_reports_when_the_service_is_down(monkeypatch):
    _fake_session(monkeypatch, status=503, body=b"unavailable")
    with pytest.raises(DocumentError, match="failed with status 503"):
        await render_mermaid("flowchart TD\n  A --> B")


async def test_render_reports_when_the_service_is_unreachable(monkeypatch):
    _fake_session(monkeypatch, error=aiohttp.ClientConnectionError("no route"))
    with pytest.raises(DocumentError, match="could not be reached"):
        await render_mermaid("flowchart TD\n  A --> B")


async def test_render_rejects_a_cut_off_image(monkeypatch):
    _fake_session(monkeypatch, body=PNG[:40])
    with pytest.raises(DocumentError, match="broken image"):
        await render_mermaid("flowchart TD\n  A --> B")


async def test_render_refuses_empty_source():
    with pytest.raises(DocumentError, match="empty"):
        await render_mermaid("   ")


# ── labels the model writes without quotes ────────────────────────────────────

def test_labels_that_hold_brackets_are_quoted():
    fixed = fix_flowchart_labels(
        "flowchart TD\n  A[split -> Arrays.stream(words)] --> B{ok (maybe)?}\n  B --> C(call f(x))\n  C --> D[list[0]]")
    assert 'A["split -#gt; Arrays.stream(words)"] --> B{"ok (maybe)?"}' in fixed
    assert 'C("call f(x)")' in fixed
    assert 'D["list[0]"]' in fixed


def test_angle_brackets_are_escaped_and_line_breaks_stay():
    fixed = fix_flowchart_labels('flowchart TD\n  A[Stream<T>] -->|List<R>| B["Map<K, V><br>done"]')
    assert fixed == 'flowchart TD\n  A["Stream#lt;T#gt;"] -->|"List#lt;R#gt;"| B["Map#lt;K, V#gt;<br>done"]'


def test_statements_on_one_line_are_fixed_too():
    fixed = fix_flowchart_labels('flowchart TD; A[Input Stream<T>]-->B{mapper: T}; B-->C[say "hi"]')
    assert fixed == 'flowchart TD; A["Input Stream#lt;T#gt;"]-->B{"mapper: T"}; B-->C["say #quot;hi#quot;"]'


def test_every_node_shape_keeps_its_brackets():
    source = "flowchart LR\n  A([a]) --> B[[b]] --> C[(c)] --> D((d)) --> E{{e}} --> F[/f/] --> G(((g)))"
    assert fix_flowchart_labels(source) == (
        'flowchart LR\n  A(["a"]) --> B[["b"]] --> C[("c")] --> D(("d")) --> E{{"e"}} --> F[/"f"/] --> G((("g")))')


def test_statements_without_labels_are_left_alone():
    source = "flowchart TD\n  A --> B\n  style A fill:#f9f,stroke:#333\n  click A call callback()\n  A & B --> C:::big"
    assert fix_flowchart_labels(source) == source


def test_a_label_that_never_closes_is_left_for_the_parser():
    assert fix_flowchart_labels("flowchart TD\n  A[oops\n  B[fine]") == 'flowchart TD\n  A[oops\n  B["fine"]'


def test_other_diagram_types_are_left_alone():
    source = "sequenceDiagram\n  Alice->>Bob: hello (again)"
    assert fix_flowchart_labels(source) == source


def test_sequence_participant_names_lose_their_quotes():
    source = 'sequenceDiagram\n  participant U as User\n  participant A as "Our App (Client)"\n  actor G as "Google"\n  U->>A: click "Sign in"'
    assert fix_sequence_names(source) == (
        'sequenceDiagram\n  participant U as User\n  participant A as Our App (Client)\n  actor G as Google\n  U->>A: click "Sign in"')


def test_quotes_outside_sequence_diagrams_are_kept():
    source = 'flowchart TD\n  A["participant X as \\"Y\\""] --> B'
    assert fix_sequence_names(source) == source


async def test_render_asks_for_readable_notes(monkeypatch):
    seen = _fake_session(monkeypatch)
    await render_mermaid("sequenceDiagram\n  A->>B: hi\n  Note over A,B: a long note")
    sent = json.loads(zlib.decompress(base64.urlsafe_b64decode(seen["url"].split("pako:")[1].split("?")[0])))
    assert sent["mermaid"]["sequence"] == {"wrap": True, "actorMargin": 140}
    assert sent["mermaid"]["themeVariables"]["noteTextColor"] == "#222222"


async def test_render_sends_the_fixed_labels(monkeypatch):
    seen = _fake_session(monkeypatch)
    await render_mermaid("flowchart TD\n  A[stream(words)] --> B")
    sent = json.loads(zlib.decompress(base64.urlsafe_b64decode(seen["url"].split("pako:")[1].split("?")[0])))
    assert sent["code"] == 'flowchart TD\n  A["stream(words)"] --> B'


async def test_render_tries_the_source_as_written_when_the_fixed_one_is_refused(monkeypatch):
    request = AsyncMock(side_effect=[DiagramSyntaxError("refused"), PNG])
    monkeypatch.setattr(document_builder, "_request_diagram", request)
    source = "flowchart TD\n  A[stream(words)] --> B"
    assert await render_mermaid(source) == PNG
    assert [call.args[0] for call in request.await_args_list] == [fix_flowchart_labels(source), source]


async def test_render_reports_the_error_for_the_source_as_written(monkeypatch):
    request = AsyncMock(side_effect=[DiagramSyntaxError("error in the fixed source"),
                                     DiagramSyntaxError("error in the source as written")])
    monkeypatch.setattr(document_builder, "_request_diagram", request)
    with pytest.raises(DocumentError, match="as written"):
        await render_mermaid("flowchart TD\n  A[stream(words)] --> B")


# ── mermaid source pasted into a chat message ─────────────────────────────────

def test_split_finds_a_diagram_in_a_code_block():
    assert split_mermaid("here:\n```mermaid\nflowchart TD\n  A --> B\n```\nlmk") == ("flowchart TD\n  A --> B", "here:\n\nlmk")
    assert split_mermaid("```\nsequenceDiagram\n  A->>B: hi\n```") == ("sequenceDiagram\n  A->>B: hi", "")


def test_split_finds_loose_source_at_the_end_of_a_message():
    text = "aight fixed it, paste this into mermaid.live or whatever: flowchart TD; A[Input]-->B[Output]"
    assert split_mermaid(text) == ("flowchart TD; A[Input]-->B[Output]",
                                   "aight fixed it, paste this into mermaid.live or whatever:")


def test_split_ignores_ordinary_messages_and_other_code():
    assert split_mermaid("yo whats good") is None
    assert split_mermaid("a flowchart TD goes top down, LR goes sideways") is None
    assert split_mermaid("```python\nprint('graph LR')\n```") is None


# ── the backup renderer ───────────────────────────────────────────────────────

async def test_render_uses_the_backup_service_when_the_first_is_down(monkeypatch):
    fetch = AsyncMock(side_effect=[DocumentError("the diagram renderer (mermaid.ink) failed with status 503, try again later"), PNG])
    monkeypatch.setattr(document_builder, "_fetch_diagram", fetch)
    assert await render_mermaid("sequenceDiagram\n  A->>B: hi") == PNG
    first, second = [call.args for call in fetch.await_args_list]
    assert first[0].startswith("https://mermaid.ink/img/pako:") and second[0].startswith("https://kroki.io/mermaid/png/")
    assert second[2] == document_builder.KROKI_TIMEOUT_SECONDS  # the backup hangs when it fails, so it gets less time
    sent = zlib.decompress(base64.urlsafe_b64decode(second[0].rsplit("/", 1)[1])).decode("utf-8")
    assert sent.startswith("%%{init: ") and sent.endswith("sequenceDiagram\n  A->>B: hi")


async def test_backup_service_gets_only_the_theme_for_a_flowchart(monkeypatch):
    fetch = AsyncMock(side_effect=[DocumentError("down"), PNG])
    monkeypatch.setattr(document_builder, "_fetch_diagram", fetch)
    await render_mermaid("flowchart TD\n  A --> B")
    sent = zlib.decompress(base64.urlsafe_b64decode(fetch.await_args_list[1].args[0].rsplit("/", 1)[1])).decode("utf-8")
    assert sent.startswith('%%{init: {"theme": "neutral"}}%%\nflowchart TD')


async def test_backup_service_gets_a_second_try(monkeypatch):
    fetch = AsyncMock(side_effect=[DocumentError("down"), DocumentError("the backup hung"), PNG])
    monkeypatch.setattr(document_builder, "_fetch_diagram", fetch)
    assert await render_mermaid("flowchart TD\n  A --> B") == PNG
    assert fetch.await_count == 3


async def test_render_reports_the_first_error_when_both_services_fail(monkeypatch):
    backup_down = DocumentError("the diagram renderer (kroki.io) failed with status 500, try again later")
    fetch = AsyncMock(side_effect=[DocumentError("the diagram renderer (mermaid.ink) could not be reached, try again later"),
                                   backup_down, backup_down])
    monkeypatch.setattr(document_builder, "_fetch_diagram", fetch)
    with pytest.raises(DocumentError, match="mermaid.ink"):
        await render_mermaid("flowchart TD\n  A --> B")
    assert fetch.await_count == 1 + document_builder.KROKI_TRIES


async def test_a_syntax_error_does_not_go_to_the_backup_service(monkeypatch):
    fetch = AsyncMock(side_effect=DiagramSyntaxError("the mermaid source has a syntax error: line 2"))
    monkeypatch.setattr(document_builder, "_fetch_diagram", fetch)
    with pytest.raises(DiagramSyntaxError):
        await render_mermaid("flowchart TD\n  A --> B")
    assert fetch.await_count == 1


# ── wide diagrams ─────────────────────────────────────────────────────────────

def _png_of(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


async def test_a_wide_flowchart_is_drawn_the_other_way(monkeypatch):
    strip, tall = _png_of(1400, 175), _png_of(1400, 2300)
    renderer = AsyncMock(side_effect=[strip, tall])
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    built = await build_diagram("Flow", "flowchart TD\n  A --> B")
    assert built.data == tall
    assert renderer.await_args_list[1].args[0] == "flowchart LR\n  A --> B"


async def test_a_long_left_to_right_chain_is_drawn_top_down(monkeypatch):
    renderer = AsyncMock(side_effect=[_png_of(1400, 120), _png_of(1400, 1900)])
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    await build_diagram("Flow", "graph LR\n  A --> B")
    assert renderer.await_args_list[1].args[0] == "graph TD\n  A --> B"


async def test_the_first_drawing_is_kept_when_turning_it_does_not_help(monkeypatch):
    strip = _png_of(1400, 300)
    renderer = AsyncMock(side_effect=[strip, _png_of(1400, 150)])
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    assert (await build_diagram("Flow", "flowchart TD\n  A --> B")).data == strip

    renderer = AsyncMock(side_effect=[strip, DocumentError("renderer down")])
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    assert (await build_diagram("Flow", "flowchart TD\n  A --> B")).data == strip


async def test_normal_and_non_flowchart_diagrams_are_drawn_once(monkeypatch):
    renderer = AsyncMock(return_value=_png_of(1400, 900))
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    await build_diagram("Flow", "flowchart TD\n  A --> B")
    assert renderer.await_count == 1

    renderer = AsyncMock(return_value=_png_of(1400, 200))
    monkeypatch.setattr(document_builder, "render_mermaid", renderer)
    await build_diagram("Timeline", "gantt\n  title Plan")
    assert renderer.await_count == 1


async def test_build_diagram_names_the_file_from_the_title(fake_renderer):
    built = await build_diagram("Approval path", "flowchart TD\n  A --> B")
    assert built.filename == "approval-path.png"
    assert built.data == PNG


# ── filler sentences and page limits ──────────────────────────────────────────

def test_filler_openers_and_closers_are_removed():
    from src.document_builder import strip_filler
    text = "Dear Dr. Narin,\n\nI hope you are well. I was sick and need two more days.\n\nPlease do not hesitate to contact me if you have questions.\n\nSincerely,\nSam"
    assert strip_filler(text) == "Dear Dr. Narin,\n\nI was sick and need two more days.\n\nSincerely,\nSam"


def test_filler_variants_are_removed():
    from src.document_builder import strip_filler
    assert strip_filler("I hope this email finds you well. The invoice is attached.") == "The invoice is attached."
    assert strip_filler("Here is the plan. I hope this helps!") == "Here is the plan. "
    assert strip_filler("I hope you're doing well. Quick question.") == "Quick question."


def test_sentences_that_only_look_like_filler_are_kept():
    from src.document_builder import strip_filler
    kept = "I hope you are well enough to travel on Friday. I hope this helps the team decide."
    assert strip_filler(kept) == kept


def test_document_cleanup_also_removes_filler():
    assert clean_document_text("# Letter\n\nI hope this message finds you well. The rent is due.") == "# Letter\n\nThe rent is due."


LONG_DOC = "# Long\n\n" + "\n\n".join(
    f"## Section {i}\n\n" + "This sentence fills the page with ordinary words. " * 9 for i in range(1, 9)
)


async def test_page_limit_shrinks_a_pdf_that_is_slightly_too_long():
    normal = await build_document("pdf", "Long", LONG_DOC)
    fitted = await build_document("pdf", "Long", LONG_DOC, max_pages=1)
    assert len(PdfReader(io.BytesIO(normal.data)).pages) == 2
    assert len(PdfReader(io.BytesIO(fitted.data)).pages) == 1


async def test_page_limit_that_cannot_be_met_keeps_the_normal_size():
    huge = LONG_DOC + "\n\n" + LONG_DOC.replace("# Long", "## More") + "\n\n" + LONG_DOC.replace("# Long", "## Even more")
    normal = await build_document("pdf", "Long", huge)
    limited = await build_document("pdf", "Long", huge, max_pages=1)
    assert len(PdfReader(io.BytesIO(limited.data)).pages) == len(PdfReader(io.BytesIO(normal.data)).pages) > 1


# ── titles and the Word template ──────────────────────────────────────────────

def test_clean_title_turns_a_filename_into_a_title():
    from src.document_builder import clean_title
    assert clean_title("Deployment Process.pdf") == "Deployment Process"
    assert clean_title("CSC318_Study_Plan.docx") == "CSC318 Study Plan"
    assert clean_title("  Plain title  ") == "Plain title"
    assert clean_title("") == ""


async def test_docx_uses_the_house_template():
    built = await build_document("docx", "Quarterly report", DOC)
    styles = zipfile.ZipFile(io.BytesIO(built.data)).read("word/styles.xml").decode("utf-8")
    assert "4F81BD" not in styles and "345A8A" not in styles   # no default blue headings
    assert "<w:tblBorders>" in styles                           # tables have a grid
    assert 'w:fill="EFEFEF"' in styles                          # shaded header row


def test_word_template_is_shipped_with_the_code():
    assert os.path.isfile(document_builder.DOCX_TEMPLATE)


async def test_docx_tables_span_the_page_width():
    built = await build_document("docx", "Quarterly report", DOC)
    assert '<w:tblW w:type="pct" w:w="5000" />' in docx_xml(built.data)
    assert zipfile.ZipFile(io.BytesIO(built.data)).testzip() is None
