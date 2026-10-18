import asyncio
import json
import os
from pathlib import Path

import discord
import numpy as np
from discord import app_commands
from discord.ext import commands
from groq import AsyncGroq
from sentence_transformers import SentenceTransformer

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "faq"
EMBED_MODEL = "all-MiniLM-L6-v2"
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
        json.dump({"chunks": chunks, "embeddings": embeddings.tolist()}, f)


def load_knowledge_base(guild_id):
    path = _path(guild_id)
    if not path.exists():
        return None, None
    with open(path, "r") as f:
        data = json.load(f)
    return data["chunks"], np.array(data["embeddings"])


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


class FAQ(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.model = None
        self.groq = None

    async def cog_load(self):
        # Loading the embedding model takes a few seconds. Doing it in a
        # thread keeps the bot's event loop free while it loads.
        self.model = await asyncio.to_thread(SentenceTransformer, EMBED_MODEL)
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            self.groq = AsyncGroq(api_key=api_key)
        else:
            print("[faq] GROQ_API_KEY is not set — /ask will be unavailable.")

    async def embed(self, texts):
        # model.encode is slow, CPU-heavy work. Running it in a thread means
        # the rest of the bot (other commands, other servers) keeps working
        # while it runs, instead of freezing until it finishes.
        return await asyncio.to_thread(self.model.encode, texts)

    @app_commands.command(name="faqload", description="Load this server's FAQ knowledge base (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def faqload(self, interaction: discord.Interaction, file: discord.Attachment):
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

        embeddings = await self.embed(chunks)
        await asyncio.to_thread(save_knowledge_base, interaction.guild.id, chunks, embeddings)
        await interaction.followup.send(f"Loaded {len(chunks)} chunks into this server's FAQ.", ephemeral=True)

    @app_commands.command(name="ask", description="Ask a question from this server's FAQ")
    @app_commands.guild_only()
    async def ask(self, interaction: discord.Interaction, question: str):
        if self.groq is None:
            await interaction.response.send_message("The FAQ isn't available — the bot owner hasn't set a Groq API key.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            chunks, embeddings = await asyncio.to_thread(load_knowledge_base, interaction.guild.id)

            if chunks is None:
                await interaction.followup.send("No FAQ set up yet — an admin needs to run `/faqload` first.")
                return

            query_vec = (await self.embed([question]))[0]
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
