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
- Self-serve role assignment — each server picks its own roles with `/roleadd`, then members use a reaction-role picker *or* the `/role` slash command; both toggle the same roles
- Moderation basics — kick, ban, timeout-based mute, warnings, and an automated spam filter, with role-hierarchy checks so a moderator can't act on someone ranked above them
- Utility commands — server info, quick polls, timed reminders
- **Respect points** — saying thanks to someone (@mention or reply) gives them +1; points add up to six levels (Newbie → Legend), level-ups are announced, and `/leaderboard` shows the server's top 10
- **AI-powered FAQ** — an admin uploads a `.txt`/`.md` knowledge base with `/faqload`, and members ask questions in plain English with `/ask`; answers are retrieved from that server's own content, not hardcoded or hallucinated
- Per-server isolation — every server gets its own FAQ knowledge base, role list, role-picker message, warnings and spam counts, so the same bot can serve multiple clients' servers with completely separate content
- Local embeddings (`sentence-transformers`) — no API cost for the retrieval step
- Free-tier LLM inference via Groq — no paid API spend required to run or demo
- Restart-safe — role lists, role-picker messages and FAQ knowledge bases persist across restarts and redeploys
- Non-blocking AI calls — embeddings run in a background thread and the Groq call is async, so one `/ask` never freezes the bot for other servers
- Containerized with Docker for consistent, portable deployment
---
## Project Structure
```
hearth/
│
├── cogs/
│   ├── onboarding.py         # welcome DM + server-join announcement
│   ├── roles.py              # per-server role setup, reaction picker + /role
│   ├── moderation.py         # kick/ban/mute/warn + spam filter
│   ├── utility.py            # serverinfo, poll, remind
│   ├── respect.py            # thank-you points, levels, leaderboard (SQLite)
│   └── faq.py                # /faqload + /ask — the RAG pipeline
│
├── data/                     # persisted role settings, FAQ knowledge bases, respect.db (gitignored)
│
├── main.py                   # bot entrypoint, intents, cog loader, error handler
├── Dockerfile
├── .dockerignore
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
---
## Setting Up Roles

Each server chooses its own pickable roles — no code changes needed:

1. `/roleadd emoji:🎨 role:@Artists` — repeat for each role (up to 20)
2. `/rolesetup` — posts the picker message in the current channel

Members then react to the picker or use `/role`. Admins can check the list
with `/rolelist` and drop a role with `/roleremove`; run `/rolesetup` again to
post an updated picker. Hearth refuses roles it can't safely hand out:
`@everyone`, bot-managed roles, roles with Administrator permission, roles
above Hearth's own role, and roles above the admin's own highest role.

---
## How Respect Points Work

When a message says thanks (*thanks*, *thank you*, *ty*, *thx*…) and is aimed
at someone — they're @mentioned, or it's a reply to their message — they get
+1 respect. To keep it fair:

- You can't thank yourself or a bot, and "no thanks" doesn't count
- The same person can give the same person +1 only once an hour
- One message counts for at most 5 people

Points add up to levels, and each new level is announced in the channel:

| Respect | Level |
|---|---|
| 1 | 1 — Newbie |
| 25 | 2 — Helper |
| 50 | 3 — Contributor |
| 100 | 4 — Regular |
| 500 | 5 — Veteran |
| 1000 | 6 — Legend |

Scores are kept per server in a small SQLite database (`data/respect.db`).

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
| `/roleadd` | Add a pickable role with its emoji (admin only) |
| `/roleremove` | Remove a pickable role (admin only) |
| `/rolelist` | Show this server's pickable roles |
| `/rolesetup` | Post the reaction-role picker (admin only) |
| `/role` | Toggle one of the pickable roles on/off for yourself |
| `/kick`, `/ban`, `/mute`, `/warn` | Moderation actions (permission-gated) |
| `/serverinfo` | Show server stats |
| `/respect` | Show your (or someone's) respect points, level and rank |
| `/leaderboard` | Top 10 most respected members in this server |
| `/poll` | Post a quick 👍/👎 poll |
| `/remind` | Get DM'd a reminder after N minutes (up to one week) |
| `/faqload` | Load a server's FAQ knowledge base from a file (admin only) |
| `/ask` | Ask a question, answered from that server's FAQ |
---
## Tech Stack
| Tool | Purpose |
|---|---|
| `discord.py` | Bot framework, slash commands |
| `sentence-transformers` | Local text embeddings |
| `numpy` | Cosine similarity search |
| `sqlite3` | Respect points storage (built into Python) |
| `groq` | LLM inference via Groq (free tier) |
| `python-dotenv` | Environment variable loading |
| `Docker` | Containerized, portable deployment |
---
## Use Case
Built as a reference implementation for common Discord bot use cases —
onboarding, community management, moderation, support — rather than a
single-purpose bot. A server admin can drop Hearth into their own server and
set it up with their own roles (`/roleadd`, `/rolesetup`) and FAQ content
(`/faqload`) from inside Discord, no code changes required.

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
- Spam filter and warnings are kept per server, in memory; moderation history does not currently persist across restarts
- Reminders are held in memory too — a reminder still waiting when the bot restarts is lost
- Servers set up with an older version of Hearth (hardcoded Creative/Gaming/Dev roles) are carried over automatically the first time they're used
---
## Author
**Manish Pandeya** · [github.com/maniesh-lab](https://github.com/maniesh-lab)