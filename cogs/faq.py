"""
The FAQ system: /faqload stores a server's knowledge base, /ask answers from it.

Embeddings (text -> numbers that capture meaning) come from Google's Gemini
embeddings API instead of a model running inside the bot. That keeps Hearth
at roughly 100 MB of RAM, so it fits on small free hosts. Answers come from
Groq, using only the chunks that best match the question.
"""

import asyncio
import json
import os
from pathlib import Path

import discord
import numpy as np
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import types
from groq import AsyncGroq

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "faq"
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768        # smaller vectors = smaller files; plenty for an FAQ
EMBED_BATCH = 100      # texts sent per API request
LLM_MODEL = "openai/gpt-oss-20b"
TOP_K = 3
MAX_FILE_BYTES = 1_000_000  # 1 MB of text is plenty for an FAQ
DISCORD_MESSAGE_LIMIT = 2000


def chunk_text(text, chunk_size=200):
    words = text.split()
    return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]


def _path(guild_id):
    return DATA_DIR / f"{guild_id}.json"


def save_knowledge_base(guild_id, chunks, embeddings):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path(guild_id), "w") as f:
        # The model name is saved too: vectors from different models can't
        # be compared, so an FAQ made with another model must be reloaded.
        json.dump({"model": EMBED_MODEL, "chunks": chunks, "embeddings": embeddings.tolist()}, f)


def load_knowledge_base(guild_id):
    """Returns (chunks, embeddings, status). status is "ok", "missing" or "outdated"."""
    path = _path(guild_id)
    if not path.exists():
        return None, None, "missing"
    with open(path, "r") as f:
        data = json.load(f)
    if data.get("model") != EMBED_MODEL:
        return None, None, "outdated"
    return data["chunks"], np.array(data["embeddings"], dtype=np.float32), "ok"


def cosine_similarity(query_vec, matrix):
    query_norm = query_vec / np.linalg.norm(query_vec)
    matrix_norm = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix_norm @ query_norm


def fit_discord_limit(text):
    """Discord rejects messages over 2000 characters, so trim long answers."""
    text = (text or "").strip() or "I couldn't come up with an answer for that."
    if len(text) <= DISCORD_MESSAGE_LIMIT:
        return text
    return text[: DISCORD_MESSAGE_LIMIT - 1] + "…"


class GeminiEmbedder:
    """Turns text into vectors using the Gemini embeddings API."""

    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)

    async def embed(self, texts, task_type):
        """task_type: "RETRIEVAL_DOCUMENT" for FAQ chunks, "RETRIEVAL_QUERY" for questions."""
        vectors = []
        for start in range(0, len(texts), EMBED_BATCH):
            result = await self.client.aio.models.embed_content(
                model=EMBED_MODEL,
                contents=texts[start:start + EMBED_BATCH],
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBED_DIM),
            )
            vectors.extend(e.values for e in result.embeddings)
        return np.array(vectors, dtype=np.float32)


class FAQ(commands.Cog):
    def __init__(self, bot, embedder=None, groq=None):
        self.bot = bot
        self.embedder = embedder
        self.groq = groq

    async def cog_load(self):
        if self.embedder is None:
            key = os.getenv("GEMINI_API_KEY")
            if key:
                self.embedder = GeminiEmbedder(key)
            else:
                print("[faq] GEMINI_API_KEY is not set — /faqload and /ask will be unavailable.")
        if self.groq is None:
            key = os.getenv("GROQ_API_KEY")
            if key:
                self.groq = AsyncGroq(api_key=key)
            else:
                print("[faq] GROQ_API_KEY is not set — /ask will be unavailable.")

    @app_commands.command(name="faqload", description="Load this server's FAQ knowledge base (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def faqload(self, interaction: discord.Interaction, file: discord.Attachment):
        if self.embedder is None:
            await interaction.response.send_message("The FAQ isn't available — the bot owner hasn't set a Gemini API key.", ephemeral=True)
            return
        if not file.filename.lower().endswith((".txt", ".md")):
            await interaction.response.send_message("Please attach a .txt or .md file.", ephemeral=True)
            return
        if file.size > MAX_FILE_BYTES:
            await interaction.response.send_message("That file is too big — keep it under 1 MB.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            content = (await file.read()).decode("utf-8")
        except UnicodeDecodeError:
            await interaction.followup.send("I couldn't read that file — please save it as UTF-8 text and try again.", ephemeral=True)
            return

        chunks = chunk_text(content)
        if not chunks:
            await interaction.followup.send("That file has no text in it.", ephemeral=True)
            return

        try:
            embeddings = await self.embedder.embed(chunks, "RETRIEVAL_DOCUMENT")
        except Exception as e:
            print(f"[faq] /faqload embedding failed: {e!r}")
            await interaction.followup.send("Couldn't process the FAQ right now — try again in a moment.", ephemeral=True)
            return

        await asyncio.to_thread(save_knowledge_base, interaction.guild.id, chunks, embeddings)
        await interaction.followup.send(f"Loaded {len(chunks)} chunks into this server's FAQ.", ephemeral=True)

    @app_commands.command(name="ask", description="Ask a question from this server's FAQ")
    @app_commands.guild_only()
    async def ask(self, interaction: discord.Interaction, question: str):
        if self.embedder is None or self.groq is None:
            await interaction.response.send_message("The FAQ isn't available — the bot owner hasn't set its API keys.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            chunks, embeddings, status = await asyncio.to_thread(load_knowledge_base, interaction.guild.id)

            if status == "missing":
                await interaction.followup.send("No FAQ set up yet — an admin needs to run `/faqload` first.")
                return
            if status == "outdated":
                await interaction.followup.send("This server's FAQ was loaded with an older version of Hearth — an admin needs to run `/faqload` again.")
                return

            query_vec = (await self.embedder.embed([question], "RETRIEVAL_QUERY"))[0]
            scores = cosine_similarity(query_vec, embeddings)
            top_indices = np.argsort(scores)[-TOP_K:][::-1]
            context = "\n\n".join(chunks[i] for i in top_indices)

            response = await self.groq.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "Answer only using the provided context. If the context doesn't contain the answer, say you don't have that information."},
                    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
                ],
            )
            await interaction.followup.send(fit_discord_limit(response.choices[0].message.content))
        except Exception as e:
            print(f"[faq] /ask failed: {e!r}")
            await interaction.followup.send("Something went wrong answering that — try again in a moment.")


async def setup(bot):
    await bot.add_cog(FAQ(bot))
