# Hearth
A Discord bot that handles the basics a server needs — onboarding, roles,
moderation, and utility commands — plus one thing most bots at this scale
don't have: an AI-powered FAQ system that actually understands what it's
answering, instead of matching keywords.
Built to demonstrate backend and applied-AI skills together: `discord.py`
for the bot layer, a hand-rolled retrieval pipeline (`sentence-transformers`
+ plain `numpy` cosine similarity, no vector database) for the FAQ system,
and Groq for fast, free-tier LLM inference — structured as independent cogs
so any single feature can be reused or dropped into another bot.

---
## Features
- Welcome DM and server-join announcement when a new member joins
- Self-serve role assignment — reaction roles *and* a `/role` slash command, both toggle the same roles
- Moderation basics — kick, ban, timeout-based mute, warnings, and an automated spam filter
- Utility commands — server info, quick polls, timed reminders
- **AI-powered FAQ** — an admin uploads a `.txt`/`.md` knowledge base with `/faqload`, and members ask questions in plain English with `/ask`; answers are retrieved from that server's own content, not hardcoded or hallucinated
- Per-server isolation — every server gets its own FAQ knowledge base and its own role-picker message, so the same bot can serve multiple clients' servers with completely separate content
- Local embeddings (`sentence-transformers`) — no API cost for the retrieval step
- Free-tier LLM inference via Groq — no paid API spend required to run or demo
- Restart-safe — role-picker messages and FAQ knowledge bases persist across restarts and redeploys
- Containerized with Docker for consistent, portable deployment
---
## Project Structure
```
hearth/
│
├── cogs/
│   ├── onboarding.py         # welcome DM + server-join announcement
│   ├── roles.py              # reaction roles + /role, per-server persistence
│   ├── moderation.py         # kick/ban/mute/warn + spam filter
│   ├── utility.py            # serverinfo, poll, remind
│   └── faq.py                # /faqload + /ask — the RAG pipeline
│
├── data/                     # persisted role-message IDs + FAQ knowledge bases (gitignored)
│
├── main.py                   # bot entrypoint, intents, cog loader
├── Dockerfile
├── .dockerignore
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
---
## How It Works — The FAQ System
1. **Loading** — an admin runs `/faqload` and attaches a `.txt`/`.md` file; the content is split into ~200-word chunks
2. **Embedding** — each chunk is converted into a vector locally via `sentence-transformers` (`all-MiniLM-L6-v2`) — no API call, runs on CPU
3. **Storage** — chunks and their vectors are saved to a small JSON file per server (`data/faq/{guild_id}.json`), so different servers' content never mixes
4. **Retrieval** — when someone runs `/ask`, their question is embedded the same way, and a plain cosine-similarity check (no vector database needed at this scale) finds the 3 closest chunks
5. **Generation** — those chunks are inserted into a prompt instructing the LLM to answer *only* from that context, then sent to Groq for a fast, grounded response
---
## How to Run
**1. Clone the repo**
```bash
git clone https://github.com/maniesh-lab/hearth
cd hearth
```
**2. Create and activate a virtual environment**
```bash
python -m venv venv
source venv/bin/activate
```
**3. Install dependencies**
```bash
pip install -r requirements.txt
```
**4. Set up environment variables**
```bash
cp .env.example .env
```
Add your Discord bot token (from the [Discord Developer Portal](https://discord.com/developers/applications)) and a free Groq API key (from [console.groq.com](https://console.groq.com)) to `.env`.

**5. Run the bot**
```bash
python main.py
```

**Or, with Docker:**
```bash
docker build -t hearth .
docker run --env-file .env -v $(pwd)/data:/app/data --name hearth hearth
```
---
## Commands
| Command | Description |
|---|---|
| `/ping` | Health check |
| `/rolesetup` | Post the reaction-role picker (admin only) |
| `/role` | Toggle a role on/off for yourself |
| `/kick`, `/ban`, `/mute`, `/warn` | Moderation actions (permission-gated) |
| `/serverinfo` | Show server stats |
| `/poll` | Post a quick 👍/👎 poll |
| `/remind` | Get DM'd a reminder after N minutes |
| `/faqload` | Load a server's FAQ knowledge base from a file (admin only) |
| `/ask` | Ask a question, answered from that server's FAQ |
---
## Tech Stack
| Tool | Purpose |
|---|---|
| `discord.py` | Bot framework, slash commands |
| `sentence-transformers` | Local text embeddings |
| `numpy` | Cosine similarity search |
| `groq` | LLM inference via Groq (free tier) |
| `python-dotenv` | Environment variable loading |
| `Docker` | Containerized, portable deployment |
---
## Use Case
Built as a reference implementation for common Discord bot use cases —
onboarding, community management, moderation, support — rather than a
single-purpose bot. A server admin can drop Hearth into their own server and
have it fully configured with their own roles and FAQ content in two
commands, no code changes required.

---
## Known Limitation
The FAQ system retrieves a handful of relevant *chunks*, not the whole
knowledge base at once. This makes it strong at specific factual questions
but structurally unable to answer whole-document questions like "how many
FAQ entries are there?" — no single chunk contains that answer. The bot is
designed to say it doesn't have that information rather than guess.

---
## Notes
- Each server's FAQ content and role-picker message are fully isolated — one server's setup never affects another's
- No vector database — cosine similarity over plain `numpy` arrays is sufficient at this scale and avoids unnecessary dependency weight
- Spam filter and warnings are in-memory per session; moderation history does not currently persist across restarts
---
## Author
**Manish Pandeya** · [github.com/maniesh-lab](https://github.com/maniesh-lab)