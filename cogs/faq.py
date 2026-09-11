import discord
from discord.ext import commands
from discord import app_commands
from sentence_transformers import SentenceTransformer
from groq import Groq
import numpy as np
import json
import os
from pathlib import Path

model = SentenceTransformer("all-MiniLM-L6-v2")
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "faq"

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

class FAQ(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="faqload", description="Load this server's FAQ knowledge base (admin only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def faqload(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.endswith((".txt", ".md")):
            await interaction.response.send_message("Please attach a .txt or .md file.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        content = (await file.read()).decode("utf-8")
        chunks = chunk_text(content)
        embeddings = model.encode(chunks)

        save_knowledge_base(interaction.guild.id, chunks, embeddings)
        await interaction.followup.send(f"Loaded {len(chunks)} chunks into this server's FAQ.", ephemeral=True)

    @app_commands.command(name="ask", description="Ask a question from this server's FAQ")
    async def ask(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        chunks, embeddings = load_knowledge_base(interaction.guild.id)

        if chunks is None:
            await interaction.followup.send("No FAQ set up yet — an admin needs to run `/faqload` first.")
            return

        query_vec = model.encode([question])[0]
        scores = cosine_similarity(query_vec, embeddings)
        top_indices = np.argsort(scores)[-3:][::-1]
        context = "\n\n".join(chunks[i] for i in top_indices)

        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": "Answer only using the provided context. If the context doesn't contain the answer, say you don't have that information."},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
            ]
        )
        await interaction.followup.send(response.choices[0].message.content)

    async def cog_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission to do that.", ephemeral=True)
        else:
            print(f"[faq] Error: {error}")

async def setup(bot):
    await bot.add_cog(FAQ(bot))