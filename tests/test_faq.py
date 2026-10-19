import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from cogs import faq
from cogs.faq import FAQ, chunk_text, cosine_similarity, fit_discord_limit
from conftest import make_guild, make_interaction, make_member, reply_text


class FakeEmbedder:
    """Turns text into a tiny 'bag of words' vector, so similar text matches."""

    VOCAB = ["refund", "days", "shipping", "free", "exam", "friday"]

    async def embed(self, texts, task_type):
        return np.array(
            [[text.lower().count(word) + 0.01 for word in self.VOCAB] for text in texts], dtype=np.float32
        )


def fake_groq(answer="ok"):
    groq = MagicMock()
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])
    groq.chat.completions.create = AsyncMock(return_value=reply)
    return groq


def attachment(name, data):
    a = MagicMock()
    a.filename = name
    a.size = len(data)
    a.read = AsyncMock(return_value=data)
    return a


@pytest.fixture
def cog(tmp_path, monkeypatch):
    monkeypatch.setattr(faq, "DATA_DIR", tmp_path / "faq")
    return FAQ(MagicMock(), embedder=FakeEmbedder(), groq=fake_groq())


def test_chunks_and_trimming():
    assert len(chunk_text("word " * 450)) == 3
    assert chunk_text("   ") == []
    assert len(fit_discord_limit("x" * 5000)) == 2000
    assert fit_discord_limit(None) == "I couldn't come up with an answer for that."


def test_cosine_similarity_ranks_the_closest_first():
    matrix = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.float32)
    scores = cosine_similarity(np.array([1, 0], dtype=np.float32), matrix)
    assert int(np.argmax(scores)) == 0


def test_load_then_ask_uses_the_matching_chunk(cog):
    admin = make_member(1)
    interaction = make_interaction(admin, make_guild())
    text = ("Refunds take 7 days. " * 60 + "Shipping is free over $50. " * 60).encode()
    asyncio.run(cog.faqload.callback(cog, interaction, attachment("faq.md", text)))
    assert "Loaded" in reply_text(interaction)

    interaction = make_interaction(admin, make_guild())
    asyncio.run(cog.ask.callback(cog, interaction, "how many days for a refund?"))
    prompt = cog.groq.chat.completions.create.await_args.kwargs["messages"][1]["content"]
    assert prompt.split("\n\n")[0].startswith("Context:\nRefunds")


@pytest.mark.parametrize(
    "name, data, expected",
    [
        ("faq.pdf", b"x", "Please attach a .txt or .md file."),
        ("faq.txt", b"\xff\xfe\x00bad", "I couldn't read that file"),
        ("faq.md", b"   ", "That file has no text in it."),
    ],
)
def test_bad_files(cog, name, data, expected):
    interaction = make_interaction(make_member(1), make_guild())
    asyncio.run(cog.faqload.callback(cog, interaction, attachment(name, data)))
    assert reply_text(interaction).startswith(expected)


def test_ask_before_any_faq(cog):
    interaction = make_interaction(make_member(1), make_guild())
    asyncio.run(cog.ask.callback(cog, interaction, "anything"))
    assert "No FAQ set up yet" in reply_text(interaction)


def test_faq_from_the_old_model_asks_for_a_reload(cog):
    old = faq.DATA_DIR / "1.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"chunks": ["a"], "embeddings": [[0.1, 0.2]]}))  # no "model" = old version
    interaction = make_interaction(make_member(1), make_guild(guild_id=1))
    asyncio.run(cog.ask.callback(cog, interaction, "anything"))
    assert "run `/faqload` again" in reply_text(interaction)


def test_missing_keys_explain_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(faq, "DATA_DIR", tmp_path / "faq")
    cog = FAQ(MagicMock(), embedder=None, groq=None)
    interaction = make_interaction(make_member(1), make_guild())
    asyncio.run(cog.ask.callback(cog, interaction, "anything"))
    assert "API keys" in reply_text(interaction)
