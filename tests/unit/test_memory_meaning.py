"""Layer 2 of the memory plan: find the memory that fits the message by meaning, not only by shared words."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory_store import MemoryStore, Place, Speaker
from src.tools.base import Turn
from src.tools.memory import AUTO_RECALL_LIMIT, RecallMemory, RememberMemory, embed_missing

SLADE = Speaker("760", "Slade")
KRUSKAL = Speaker("111", "Kruskal")
MINT = Speaker("222", "Mint")
KMUTT = Place("1280", "cs-kmutt", "55", "general")
XLRX = Place("1300", "XLRX", "66", "chat")

# Each axis is one topic. A text points along every topic it has a word for.
TOPICS = (
    ("flu", "sick", "ill", "fever", "cold", "unwell"),
    ("vegetarian", "meat", "eat", "food", "lunch", "dinner"),
    ("exam", "room", "test", "midterm", "final"),
    ("rust", "go", "python", "code", "language"),
)


def _vector(text: str):
    words = text.lower().replace("?", " ").replace(",", " ").split()
    vector = [float(sum(w.startswith(topic) for w in words for topic in group)) for group in TOPICS]
    # Never all zero, so the cosine is defined. Off-topic texts end up far from every topic.
    return vector + [0.05]


class FakeEmbedder:
    model = "fake-1"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def embed(self, text: str, task: str = "query"):
        self.calls.append((text, task))
        if self.fail:
            raise RuntimeError("embedding service down")
        return _vector(text), 0.000001


@pytest.fixture
def store(database):
    return MemoryStore(database)


def _teach(store, content, about="", kind="claim", speaker=KRUSKAL, model="fake-1"):
    saved = store.remember(content, about, kind, speaker, KMUTT)
    store.set_embedding(saved.memory_id, _vector(f"{about} {content}"), model)
    return saved.memory_id


def _age(store, memory_id, days):
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with store.db.transaction() as connection:
        connection.execute("UPDATE memories SET created_at = ? WHERE id = ?", (when, memory_id))


# ── the store ────────────────────────────────────────────────────────────────

def test_a_memory_with_no_shared_words_is_found_by_meaning(store):
    # "who was sick" shares no word with "Slade had the flu", which layer 1 could not find
    flu = _teach(store, "Slade had the flu", "Slade", "self", SLADE)
    assert store.recall("who was sick lately") == []
    found = store.recall("who was sick lately", vector=_vector("who was sick lately"), model="fake-1")
    assert [m.id for m in found] == [flu]


def test_memories_about_something_else_are_left_out(store):
    _teach(store, "Slade had the flu", "Slade", "self", SLADE)
    _teach(store, "the exam is in room 410", "exam")
    found = store.recall("is anyone ill", vector=_vector("is anyone ill"), model="fake-1")
    assert [m.content for m in found] == ["Slade had the flu"]


def test_nothing_comes_back_for_a_message_about_nothing_known(store):
    _teach(store, "Slade had the flu", "Slade", "self", SLADE)
    assert store.recall("hows the weather", vector=_vector("hows the weather"), model="fake-1") == []


def test_the_closest_memory_comes_first(store):
    _teach(store, "Kruskal codes in rust and eats lunch at 1", "Kruskal")
    exact = _teach(store, "Kruskal writes rust code", "Kruskal")
    found = store.recall("what language does kruskal code in", vector=_vector("rust code language"), model="fake-1")
    assert found[0].id == exact


def test_only_memories_close_to_the_best_fit_come_back(store):
    # Real gemini-embedding-2 scores sit near 0.5 for anything, so a memory that is only fairly
    # close is noise next to one that clearly fits (run.md, 2026-11-19)
    clear = _teach(store, "the midterm is in room 410", "exam")
    _teach(store, "Kruskal eats before the exam in room 302")
    found = store.recall("which room is the exam", vector=_vector("exam room"), model="fake-1")
    assert [m.id for m in found] == [clear]


def test_a_newer_memory_wins_when_both_fit_as_well(store):
    old = _teach(store, "the midterm is in room 410", "exam")
    new = _teach(store, "the final is in room 302", "exam")
    _age(store, old, 60)
    found = store.recall("which room is the exam", vector=_vector("exam room"), model="fake-1")
    assert [m.id for m in found] == [new, old]


def test_a_confirmed_memory_wins_when_both_fit_as_well(store):
    lone = _teach(store, "the midterm is in room 410", "exam")
    backed = _teach(store, "the final is in room 302", "exam")
    store.remember("the final is in room 302", "exam", "claim", SLADE, KMUTT)
    _age(store, backed, 1)
    _age(store, lone, 1)
    found = store.recall("exam room", vector=_vector("exam room"), model="fake-1")
    assert found[0].id == backed and found[1].id == lone


def test_a_memory_without_a_vector_is_still_found_by_its_words(store):
    saved = store.remember("the csc340 proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    found = store.recall("when is the csc340 proposal due", vector=_vector("when is it due"), model="fake-1")
    assert [m.id for m in found] == [saved.memory_id]


def test_a_vector_from_another_model_is_not_compared(store):
    # Vectors from two models are not comparable, so an old one must not decide anything
    _teach(store, "Slade had the flu", "Slade", "self", SLADE, model="old-model")
    assert store.recall("who was sick", vector=_vector("who was sick"), model="fake-1") == []


def test_a_replaced_memory_is_not_found_by_meaning(store):
    old = _teach(store, "Slade has the flu", "Slade", "self", SLADE)
    new = store.remember("Slade is better now, the fever is gone", "Slade", "self", SLADE, KMUTT, replaces=old)
    store.set_embedding(new.memory_id, _vector("Slade is better now, the fever is gone"), "fake-1")
    found = store.recall("is slade still sick", vector=_vector("is slade still sick"), model="fake-1")
    assert [m.id for m in found] == [new.memory_id]


def test_at_most_the_limit_comes_back(store):
    for n in range(15):
        _teach(store, f"exam {n} is in room {400 + n}", "exam")
    assert len(store.recall("exam room", 10, vector=_vector("exam room"), model="fake-1")) == 10


def test_the_store_lists_memories_that_need_a_vector(store):
    plain = store.remember("slade is vegetarian", "Slade", "self", SLADE, KMUTT).memory_id
    stale = _teach(store, "the exam is in room 410", "exam", model="old-model")
    _teach(store, "kruskal writes rust", "Kruskal")
    assert sorted(m.id for m in store.missing_embeddings("fake-1")) == sorted([plain, stale])


def test_the_store_says_whether_it_has_any_memories(store):
    assert not store.has_memories()
    store.remember("slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    assert store.has_memories()


# ── the tools ────────────────────────────────────────────────────────────────

def _turn(embedder=None, user=SLADE, place=KMUTT):
    turn = Turn(query="", username=user.name, history="", attached="", images=[], user_id=user.user_id)
    turn.place = place
    if embedder is not None:
        async def embed(text, task="query"):
            vector, cost = await embedder.embed(text, task)
            turn.cost += cost
            return vector
        turn.embed_memory = embed
        turn.memory_model = embedder.model
    return turn


async def test_remember_stores_the_meaning_of_the_memory(store):
    embedder = FakeEmbedder()
    await RememberMemory(store).run({"content": "Slade had the flu", "about": "Slade", "kind": "self"},
                                    _turn(embedder))
    assert embedder.calls and embedder.calls[0][1] == "document"
    assert store.missing_embeddings("fake-1") == []


async def test_remember_still_saves_when_the_embedding_fails(store):
    result = await RememberMemory(store).run({"content": "Slade had the flu", "about": "Slade", "kind": "self"},
                                             _turn(FakeEmbedder(fail=True)))
    assert "saved as memory #" in result.text
    assert len(store.missing_embeddings("fake-1")) == 1


async def test_the_recall_tool_searches_by_meaning(store):
    _teach(store, "Slade had the flu", "Slade", "self", SLADE)
    result = await RecallMemory(store).run({"query": "who was ill"}, _turn(FakeEmbedder()))
    assert "Slade had the flu" in result.text


async def test_recall_falls_back_to_words_when_the_embedding_fails(store):
    store.remember("the csc340 proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    result = await RecallMemory(store).run({"query": "csc340 proposal"}, _turn(FakeEmbedder(fail=True)))
    assert "the csc340 proposal is due friday" in result.text


async def test_recall_with_no_embedder_still_works_by_words(store):
    store.remember("the csc340 proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    result = await RecallMemory(store).run({"query": "csc340 proposal"}, _turn())
    assert "the csc340 proposal is due friday" in result.text


async def test_embed_missing_fills_in_old_memories(store):
    store.remember("slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    _teach(store, "the exam is in room 410", "exam", model="old-model")
    embedder = FakeEmbedder()
    assert await embed_missing(store, embedder) == 2
    assert store.missing_embeddings("fake-1") == []
    assert all(task == "document" for _, task in embedder.calls)


async def test_embed_missing_stops_quietly_when_the_service_is_down(store):
    store.remember("slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    assert await embed_missing(store, FakeEmbedder(fail=True)) == 0


def test_a_reply_can_bring_up_to_ten_memories():
    assert AUTO_RECALL_LIMIT == 10


# ── a whole reply ────────────────────────────────────────────────────────────

def _message(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _handler(mock_openai_client, embedder):
    from src.ai_response_handler import AIResponseHandler
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        handler = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                                    api_key="fake_key", deployment="gpt-5-mini")
    handler.ai_client = mock_openai_client
    handler.memory_embedder = embedder
    return handler


def _sent(mock_openai_client) -> str:
    return "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])


async def test_a_reply_brings_up_a_memory_that_shares_no_words(database, mock_openai_client):
    _teach(MemoryStore(database), "Slade had the flu", "Slade", "self", SLADE)
    mock_openai_client.chat.completions.create.side_effect = [_message(content="slade had the flu, he told me")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True, place=XLRX)
    assert "Slade had the flu" in _sent(mock_openai_client)


async def test_no_embedding_is_made_when_nothing_is_remembered(database, mock_openai_client):
    embedder = FakeEmbedder()
    mock_openai_client.chat.completions.create.side_effect = [_message(content="yo")]
    handler = _handler(mock_openai_client, embedder)
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True, place=XLRX)
    assert embedder.calls == []


async def test_a_reply_still_works_when_the_embedding_service_is_down(database, mock_openai_client):
    MemoryStore(database).remember("the csc340 proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    mock_openai_client.chat.completions.create.side_effect = [_message(content="friday")]
    handler = _handler(mock_openai_client, FakeEmbedder(fail=True))
    reply = await handler.generate_reply("/when is the csc340 proposal due", "", "Mint", "222",
                                         allow_files=True, place=XLRX)
    assert reply.text == "friday"
    assert "the csc340 proposal is due friday" in _sent(mock_openai_client)


# ── the Vertex embedder ──────────────────────────────────────────────────────

def _vertex(post, model="gemini-embedding-2", location="global"):
    from src.embedder import VertexEmbedder
    return VertexEmbedder(project="proj", location=location, model=model, post=post)


def _ok(values, tokens=7):
    response = MagicMock()
    response.ok = True
    response.json.return_value = {"embedding": {"values": values}, "usageMetadata": {"promptTokenCount": tokens}}
    return response


async def test_vertex_embeddings_use_the_native_api(database):
    # Vertex's OpenAI-compatible endpoint answers 500 to embeddings, and only the global
    # region has gemini-embedding-2 (probe of 2026-11-19 in run.md)
    post = MagicMock(return_value=_ok([0.1] * 768))
    vector, _ = await _vertex(post).embed("Slade had the flu", "document")
    url, body = post.call_args[0][0], post.call_args[1]["json"]
    assert url == ("https://aiplatform.googleapis.com/v1/projects/proj/locations/global/"
                   "publishers/google/models/gemini-embedding-2:embedContent")
    assert body["content"] == {"parts": [{"text": "Slade had the flu"}]}
    assert body["taskType"] == "RETRIEVAL_DOCUMENT"
    assert body["outputDimensionality"] == 768
    assert len(vector) == 768


async def test_a_vertex_query_is_embedded_as_a_query(database):
    post = MagicMock(return_value=_ok([0.1] * 768))
    await _vertex(post).embed("who was sick")
    assert post.call_args[1]["json"]["taskType"] == "RETRIEVAL_QUERY"


async def test_a_regional_vertex_embedder_uses_the_regional_host(database):
    post = MagicMock(return_value=_ok([0.1] * 768))
    await _vertex(post, location="us-central1").embed("x")
    assert post.call_args[0][0].startswith("https://us-central1-aiplatform.googleapis.com/v1/projects/proj/locations/us-central1/")


async def test_vertex_embeddings_count_against_the_budget(database):
    from config.config import Config
    from src.usage_guard import get_usage_guard
    post = MagicMock(return_value=_ok([0.1] * 768, tokens=1_000_000))
    _, cost = await _vertex(post).embed("a very long text")
    assert cost == pytest.approx(Config.AI_MODEL_PRICES["gemini-embedding-2"][0])
    assert get_usage_guard().summary()["day_cost"] == pytest.approx(cost)


async def test_no_vertex_call_once_the_budget_is_spent(database):
    from src.usage_guard import BudgetExceededError, get_usage_guard
    guard = get_usage_guard()
    guard._state["day_cost"] = guard.daily_budget
    post = MagicMock(return_value=_ok([0.1] * 768))
    with pytest.raises(BudgetExceededError):
        await _vertex(post).embed("x")
    post.assert_not_called()


async def test_a_vertex_error_is_raised(database):
    response = MagicMock()
    response.ok = False
    response.status_code = 404
    response.text = "model not found"
    with pytest.raises(RuntimeError, match="404"):
        await _vertex(MagicMock(return_value=response)).embed("x")


def test_the_embedding_price_is_listed():
    from src.usage_guard import UsageGuard
    assert UsageGuard.prices_for("gemini-embedding-2")[0] == 0.20


def test_vertex_gets_a_vertex_embedder_by_default():
    from src.ai_provider import AiSettings
    from src.embedder import VertexEmbedder, make_embedder
    settings = AiSettings("vertex", "https://aiplatform.googleapis.com/v1/projects/proj/locations/global/endpoints/openapi",
                          "", "gemini-3.1-flash-lite", "", "")
    embedder = make_embedder(settings, client=MagicMock(), env={"VERTEX_PROJECT": "proj"})
    assert isinstance(embedder, VertexEmbedder)
    assert embedder.model == "gemini-embedding-2" and embedder.location == "global"


async def test_other_providers_embed_through_the_openai_api(database):
    from src.ai_provider import AiSettings
    from src.embedder import make_embedder
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.2] * 768)], usage=None, model="text-embedding-3-small"))
    embedder = make_embedder(AiSettings("openai", "https://x", "k", "gpt-5-mini", "", ""), client=client, env={})
    vector, _ = await embedder.embed("x", "document")
    kwargs = client.embeddings.create.call_args[1]
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["encoding_format"] == "float" and kwargs["dimensions"] == 768
    assert len(vector) == 768


def test_the_embedding_model_can_be_changed_from_env():
    from src.ai_provider import AiSettings
    from src.embedder import make_embedder
    settings = AiSettings("vertex", "", "", "gemini-3.1-flash-lite", "", "")
    embedder = make_embedder(settings, client=MagicMock(),
                             env={"VERTEX_PROJECT": "proj", "AI_MEMORY_EMBEDDING_MODEL": "gemini-embedding-001"})
    assert embedder.model == "gemini-embedding-001"
