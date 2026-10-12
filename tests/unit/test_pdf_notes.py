"""Unit tests for PDF page notes. The model is replaced by a fake describer, and the PDFs are built here."""
import asyncio
import base64
import io

import pytest
import weasyprint
from PIL import Image
from unittest.mock import AsyncMock

from config.config import Config
from src import pdf_notes
from src.usage_guard import BudgetExceededError


def _picture() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), "navy").save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _pdf(pages) -> bytes:
    """pages is a list of "text", "picture" or "empty"."""
    body = {
        "text": "<p>" + "Plain words about the plan for the next quarter. " * 12 + "</p>",
        "picture": f'<p>Chart title</p><img src="data:image/png;base64,{_picture()}">',
        "empty": "<p>&nbsp;</p>",
    }
    html = "".join(f'<div style="page-break-after:always">{body[kind]}</div>' for kind in pages)
    return weasyprint.HTML(string=html).write_pdf()


@pytest.fixture
def describer(monkeypatch):
    monkeypatch.setattr(Config, "PDF_PAGE_NOTES", "auto")
    pdf_notes.clear_cache()
    describe = AsyncMock(side_effect=lambda prompt, images: (f"## Page notes for {len(images)} pictures", 0.001))
    pdf_notes.set_describer(describe)
    yield describe
    pdf_notes.set_describer(None)
    pdf_notes.clear_cache()


def test_auto_picks_pages_with_pictures_and_nearly_empty_pages():
    data = _pdf(["text", "picture", "text", "empty"])
    # weasyprint adds a blank page after the last page break
    assert pdf_notes._pages_to_describe(data, "auto")[:2] == [1, 3]
    assert pdf_notes._pages_to_describe(data, "on")[:4] == [0, 1, 2, 3]


def test_pages_are_rendered_to_small_jpegs():
    urls = pdf_notes._render(_pdf(["picture", "text"]), [0, 1])
    assert len(urls) == 2 and all(url.startswith("data:image/jpeg;base64,") for url in urls)
    picture = Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1])))
    assert max(picture.size) == pdf_notes.PAGE_PICTURE_SIDE


async def test_notes_describe_only_the_picked_pages(describer):
    notes = await pdf_notes.page_notes(_pdf(["text", "picture", "text"]), "deck.pdf")
    assert notes == "## Page notes for 1 pictures"
    prompt, images = describer.await_args[0]
    assert "pages 2 of a document" in prompt and len(images) == 1


async def test_a_plain_text_pdf_gets_no_notes_and_costs_nothing(describer):
    data = weasyprint.HTML(string="<p>" + "Only words here, nothing to look at. " * 40 + "</p>").write_pdf()
    assert await pdf_notes.page_notes(data, "report.pdf") is None
    describer.assert_not_awaited()


async def test_the_same_file_is_described_once(describer):
    data = _pdf(["picture"])
    first = await pdf_notes.page_notes(data, "a.pdf")
    second = await pdf_notes.page_notes(data, "renamed.pdf")
    assert first == second and describer.await_count == 1


async def test_two_requests_for_a_new_file_share_one_pass(describer):
    data = _pdf(["picture"])
    results = await asyncio.gather(pdf_notes.page_notes(data, "a.pdf"), pdf_notes.page_notes(data, "a.pdf"))
    assert results[0] == results[1] and describer.await_count == 1


async def test_long_decks_are_split_into_requests(describer, monkeypatch):
    monkeypatch.setattr(pdf_notes, "PAGES_PER_REQUEST", 2)
    notes = await pdf_notes.page_notes(_pdf(["picture"] * 5), "deck.pdf")
    assert describer.await_count >= 3
    assert notes.count("## Page notes") == describer.await_count


async def test_a_failed_request_only_loses_its_own_pages(describer, monkeypatch):
    monkeypatch.setattr(pdf_notes, "PAGES_PER_REQUEST", 1)
    calls = []

    async def flaky(prompt, images):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("content filter")
        return "## Page fine", 0.0
    pdf_notes.set_describer(flaky)
    data = _pdf(["picture", "picture"])
    notes = await pdf_notes.page_notes(data, "deck.pdf")
    assert notes and "## Page fine" in notes
    # an incomplete pass is not kept, so the next question tries the lost pages again
    before = len(calls)
    await pdf_notes.page_notes(data, "deck.pdf")
    assert len(calls) > before


async def test_a_caller_that_runs_out_of_time_gets_nothing_and_the_pass_still_finishes(describer):
    release = asyncio.Event()

    async def slow(prompt, images):
        await release.wait()
        return "## Page done", 0.0
    pdf_notes.set_describer(slow)
    data = _pdf(["picture"])
    assert await pdf_notes.page_notes(data, "deck.pdf", wait_seconds=0.05) is None
    release.set()
    await asyncio.sleep(0.05)
    assert await pdf_notes.page_notes(data, "deck.pdf", wait_seconds=0.05) == "## Page done"


async def test_one_request_stops_waiting_for_notes_at_its_deadline(describer):
    import time
    from src import message_context as mc
    from tests.fakes import fake_attachment
    attachment = fake_attachment("deck.pdf", _pdf(["picture"]), "application/pdf")
    budget = mc._ImageBudget(mc.MAX_IMAGES, mc.MAX_PDF_IMAGES, notes_deadline=time.monotonic() - 1)
    lines, _ = await mc._read_attachment(attachment, budget)
    assert "what each page shows" not in "\n".join(lines)
    describer.assert_not_awaited()


async def test_no_notes_when_the_budget_is_used_up(describer):
    from datetime import date
    describer.side_effect = BudgetExceededError("daily", date(2026, 10, 12))
    assert await pdf_notes.page_notes(_pdf(["picture"]), "deck.pdf") is None


async def test_off_and_unconfigured_do_nothing(describer, monkeypatch):
    data = _pdf(["picture"])
    monkeypatch.setattr(Config, "PDF_PAGE_NOTES", "off")
    assert await pdf_notes.page_notes(data, "deck.pdf") is None
    monkeypatch.setattr(Config, "PDF_PAGE_NOTES", "auto")
    pdf_notes.set_describer(None)
    assert await pdf_notes.page_notes(data, "deck.pdf") is None
    describer.assert_not_awaited()


async def test_a_replied_to_pdf_carries_its_page_notes_and_no_pictures(describer):
    from src import message_context as mc
    from tests.fakes import fake_attachment
    attachment = fake_attachment("deck.pdf", _pdf(["picture", "text"]), "application/pdf")
    budget = mc._ImageBudget(mc.MAX_IMAGES, mc.MAX_PDF_IMAGES)
    lines, pictures = await mc._read_attachment(attachment, budget)
    block = "\n".join(lines)
    assert "followed by what each page shows" in lines[0]
    assert "--- what the pages of deck.pdf show, written from the page images ---" in block
    assert "## Page notes for 1 pictures" in block and "Plain words about the plan" in block
    assert pictures == [] and budget.pdf_images == mc.MAX_PDF_IMAGES


async def test_tldr_reads_a_pdf_the_short_way(describer):
    from src import message_context as mc
    from tests.fakes import fake_attachment
    attachment = fake_attachment("deck.pdf", _pdf(["picture"]), "application/pdf")
    lines, _ = await mc._read_attachment(attachment, mc._ImageBudget(0, mc.MAX_PDF_IMAGES), mc.TLDR_FILE_CHARS)
    assert "what each page shows" not in "\n".join(lines)
    describer.assert_not_awaited()


async def test_the_gate_is_asked_only_when_a_new_pass_starts(describer):
    from unittest.mock import MagicMock
    gate = MagicMock()
    data = _pdf(["picture"])
    await pdf_notes.page_notes(data, "deck.pdf", gate=gate)
    await pdf_notes.page_notes(data, "deck.pdf", gate=gate)  # already described: free
    assert gate.call_count == 1


async def test_a_pdf_over_the_daily_limit_is_read_the_short_way_and_says_so(describer):
    from src import message_context as mc
    from src.exceptions import RateLimitError
    from tests.fakes import fake_attachment

    def gate():
        raise RateLimitError(retry_after=3 * 3600)
    attachment = fake_attachment("deck.pdf", _pdf(["picture", "text"]), "application/pdf")
    budget = mc._ImageBudget(mc.MAX_IMAGES, mc.MAX_PDF_IMAGES, pdf_gate=gate)
    lines, _ = await mc._read_attachment(attachment, budget)
    block = "\n".join(lines)
    assert "used up their full pdf reads for today" in lines[0] and "about 3 hours" in lines[0]
    assert "what the pages of" not in block and "Plain words about the plan" in block
    describer.assert_not_awaited()


async def test_a_text_only_pdf_does_not_use_up_a_pdf_read(describer):
    import weasyprint
    from unittest.mock import MagicMock
    gate = MagicMock()
    data = weasyprint.HTML(string="<p>" + "Only words here, nothing to look at. " * 40 + "</p>").write_pdf()
    assert await pdf_notes.page_notes(data, "report.pdf", gate=gate) is None
    assert await pdf_notes.page_notes(data, "report.pdf", gate=gate) is None
    gate.assert_not_called()

