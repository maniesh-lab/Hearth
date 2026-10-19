# Hearth

[![CI](https://github.com/maniesh-lab/Hearth/actions/workflows/ci.yml/badge.svg)](https://github.com/maniesh-lab/Hearth/actions/workflows/ci.yml)

A Discord bot that handles the basics a server needs — onboarding, roles,
moderation, deadlines, respect points and utility commands — plus one thing most bots at this
scale don't have: an AI-powered FAQ system that actually understands what it's
answering, instead of matching keywords.

Built to demonstrate backend and applied-AI skills together: `discord.py`
for the bot layer, a hand-rolled retrieval pipeline (Gemini embeddings + plain
`numpy` cosine similarity, no vector database) for the FAQ, Groq for fast,
free-tier answers, and SQLite for everything the bot needs to remember —
structured as independent cogs so any single feature can be reused or dropped
into another bot. Light enough (~100 MB of RAM) to run on small free hosts.

---
## Features
- **Welcome** — a DM plus a public welcome for new members; each server picks the channel, the message, and a role given automatically on join (like "Student")
- **Self-serve roles** — each server picks its own roles with `/roleadd`; members use a reaction-role picker *or* the `/role` command
- **Moderation** — kick, ban, timeout-based mute, warnings and an automatic spam filter, with role-hierarchy checks so a moderator can't act on someone ranked above them
- **Saved warnings and a mod log** — warnings survive restarts (`/warnings`, `/clearwarnings`), and every kick, ban, mute and warning can be recorded in a log channel
- **Deadline board** — `/deadline add "Maths assignment" 2026-10-25`, and the bot reminds the channel a day before; survives restarts
- **Respect points** — saying thanks to someone (@mention or reply) gives them +1; points add up to six levels (Newbie → Legend), level-ups are announced, `/leaderboard` shows the top 10, and optional level roles show each member's level in the member list
- **AI-powered FAQ** — an admin uploads a `.txt`/`.md` knowledge base with `/faqload`, and members ask questions in plain English with `/ask`; answers come only from that server's own content
- **Utility** — server info, quick polls, personal reminders
- **Per-server isolation** — every server has its own settings, roles, FAQ, warnings, deadlines and respect points
- **Restart-safe** — everything above (except `/remind`) is saved to disk
- **Non-blocking** — embedding and LLM calls are async, so one `/ask` never freezes the bot for other servers
- **Tested** — a pytest suite covering every feature, run with lint on every push by GitHub Actions
- **Containerized** with Docker

---
## Project Structure
```
hearth/
│
├── cogs/
│   ├── onboarding.py         # welcome DM, public welcome, auto-role, /welcome settings
│   ├── roles.py              # per-server role setup, reaction picker + /role
│   ├── moderation.py         # kick/ban/mute/warn, saved warnings, mod log, spam filter
│   ├── deadlines.py          # /deadline board + reminder loop
│   ├── respect.py            # thank-you points, levels, leaderboard, level roles
│   ├── utility.py            # serverinfo, poll, remind
│   └── faq.py                # /faqload + /ask — the RAG pipeline
│
├── tests/                    # pytest suite, no Discord or API keys needed
├── .github/workflows/ci.yml  # lint + tests on every push (Python 3.10–3.13)
│
├── data/                     # hearth.db, respect.db, role settings, FAQs (gitignored)
│
├── main.py                   # bot entrypoint, intents, cog loader, error handler
├── storage.py                # the SQLite database shared by the cogs
├── Dockerfile
├── .dockerignore
├── .env.example
├── .gitignore
├── requirements.txt          # what the bot needs to run
├── requirements-dev.txt      # + pytest and ruff for development
├── pytest.ini                # tells pytest where the tests are
├── ruff.toml                 # lint rules: catches real bugs, not style
├── LICENSE
└── README.md
```

---
## How to Run
Requires Python 3.10 or newer.

**0. Create the bot on Discord (one time)**
1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application, open **Bot**, and copy its token
2. On the same page, turn on **Server Members Intent** and **Message Content Intent** — Hearth won't start without them (welcomes need the first; the spam filter and respect points need the second)
3. Invite it to your server with this link, putting your application's ID in place of `YOUR_APP_ID`:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=1099780148294
   ```
   That grants exactly what the commands use: View Channels, Send Messages, Embed Links, Read Message History, Add Reactions, Manage Roles, Kick Members, Ban Members and Moderate Members
4. In **Server Settings → Roles**, drag Hearth's role above every role it should hand out or moderate — Discord won't let a bot act on roles above its own

**1. Clone the repo**
```bash
git clone https://github.com/maniesh-lab/Hearth
cd Hearth
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
Fill in `.env` as `KEY=value`, with no spaces or quotes (so it works with both
`python main.py` and `docker run --env-file`):

| Variable | Needed for | Where to get it |
|---|---|---|
| `DISCORD_TOKEN` | Everything | Step 0 |
| `GEMINI_API_KEY` | `/faqload`, `/ask` (embeddings) | Free at [aistudio.google.com](https://aistudio.google.com) |
| `GROQ_API_KEY` | `/ask` (answers) | Free at [console.groq.com](https://console.groq.com) |
| `HEARTH_TIMEZONE` | `/deadline` dates and times | Optional, default `Asia/Kathmandu` |

Without the two AI keys the bot still runs; only the FAQ commands are unavailable.

**5. Run the bot**
```bash
python main.py
```

**Or, with Docker:**
```bash
docker build -t hearth .
docker run --env-file .env -v $(pwd)/data:/app/data --name hearth hearth
```
The `-v` part keeps the `data/` folder on your machine, so nothing is lost when
the container is rebuilt. Slash commands can take a minute to appear after the
first start.

**Run the tests**
```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

---
## Setting It Up in Your Server
Admin commands need **Manage Server** (welcome, mod log), **Manage Roles**
(roles, level roles) or **Moderate Members** (warnings); Discord hides them from everyone else.

**Welcome**
- `/welcome channel #general` — where the public welcome goes (default: the server's system channel)
- `/welcome message Hey {user}, welcome to {server}! You're member #{count}.` — `{user}`, `{server}` and `{count}` are filled in
- `/welcome role @Student` — given to everyone who joins (run it with no role to turn it off)
- `/welcome show` and `/welcome test` — check the setup, or preview it on yourself

**Roles**
1. `/roleadd emoji:🎨 role:@Artists` — repeat for each role (up to 20)
2. `/rolesetup` — posts the picker message in the current channel

Members then react to the picker or use `/role`. `/rolelist` shows the list,
`/roleremove` drops a role; run `/rolesetup` again to post an updated picker.
Hearth refuses roles it can't safely hand out: `@everyone`, bot-managed roles,
Administrator roles, and roles above Hearth's own or the admin's own.

**Mod log**
- `/modlog set #mod-log` — records every kick, ban, mute, warning, auto-mute and cleared warnings there
- `/modlog off` — stops recording

---
## How the Deadline Board Works
Anyone can add a deadline; the person who added it, or anyone with Manage
Messages, can remove it.

- `/deadline add title:"Maths assignment" date:2026-10-25` — optionally `due_time:17:00` and `channel:#assignments`
- `/deadline list` — upcoming deadlines with their numbers
- `/deadline remove number:3`

The reminder is posted in the deadline's channel a day before: **24 hours before**
if a time was given, otherwise **9:00 the day before** (a date-only deadline counts
as due at the end of that day). Dates are read in `HEARTH_TIMEZONE`, and Discord
shows every time in each member's own time zone. If the bot was offline when a
reminder was due, it's sent as soon as the bot is back — unless the deadline
itself has passed. Finished deadlines are cleaned up after a week; each server
can have up to 50 upcoming.

---
## How Respect Points Work
When a message says thanks (*thanks*, *thank you*, *ty*, *thx*…) and is aimed at
someone — they're @mentioned, or it's a reply to their message — they get +1
respect. To keep it fair:

- You can't thank yourself or a bot, and "no thanks" doesn't count
- The same person can give the same person +1 only once an hour
- One message counts for at most 5 people

Points add up to levels, and each new level is announced in the channel:

| Respect | Level |
|---|---|
| 1 | 1 — 🌱 Newbie |
| 25 | 2 — 🤝 Helper |
| 50 | 3 — 🧩 Contributor |
| 100 | 4 — ⭐ Regular |
| 500 | 5 — 🔥 Veteran |
| 1000 | 6 — 👑 Legend |

`/respect` shows anyone's points, level and rank; `/leaderboard` shows the top 10.

**Level roles (optional).** An admin with Manage Roles runs `/levelroles on`, and
Hearth creates one role per level, like `Helper (25+ respect)`, and gives every
member the role for their current level. From then on:

- When someone levels up, Hearth swaps their old level role for the new one, so
  each member has exactly one.
- The roles are set to show separately, so the member list is grouped by level.
- On servers with **Boost Level 2**, the level emoji becomes the role icon shown
  beside the member's name in chat. Without boosts, the emoji goes in the role
  name instead (`🤝 Helper (25+ respect)`).
- The roles have no color and no permissions, so names keep their usual color.
- They're created at the bottom of the role list. A member who also has a
  higher role that's shown separately (like Moderator) is listed under that one;
  drag the level roles higher in **Server Settings → Roles** if you'd rather they
  win. Hearth's own role must stay above them.

`/levelroles on` can be run again at any time to recreate a deleted level role.
`/levelroles off` deletes the level roles; everyone keeps their points and levels.

---
## How It Works — The FAQ System
1. **Loading** — an admin runs `/faqload` and attaches a `.txt`/`.md` file (up to 1 MB); the content is split into ~200-word chunks
2. **Embedding** — each chunk is turned into a 768-number vector by Google's Gemini embeddings API (`gemini-embedding-001`) — no AI model runs inside the bot, which keeps it small
3. **Storage** — chunks and vectors are saved to a small JSON file per server (`data/faq/{guild_id}.json`), so different servers' content never mixes
4. **Retrieval** — `/ask` embeds the question the same way, and a plain cosine-similarity check (no vector database needed at this scale) finds the 3 closest chunks
5. **Generation** — those chunks go into a prompt telling the LLM to answer *only* from that context, sent to Groq for a fast, grounded answer

---
## Commands
| Command | Description |
|---|---|
| `/ping` | Health check |
| `/welcome channel` · `message` · `role` · `show` · `test` | Set up how new members are welcomed (admin) |
| `/roleadd` · `/roleremove` · `/rolelist` · `/rolesetup` | Set up the pickable roles (admin) |
| `/role` | Toggle one of the pickable roles on/off for yourself |
| `/kick`, `/ban`, `/mute`, `/warn` | Moderation actions (permission-gated) |
| `/warnings` · `/clearwarnings` | See or clear a member's saved warnings (moderators) |
| `/modlog set` · `off` | Record moderation actions in a channel (admin) |
| `/deadline add` · `list` · `remove` | Shared deadlines with reminders a day before |
| `/respect` | Show someone's respect points, level and rank |
| `/leaderboard` | Top 10 most respected members in this server |
| `/levelroles on` · `off` | Give members a role for their respect level (admin) |
| `/faqload` | Load a server's FAQ knowledge base from a file (admin) |
| `/ask` | Ask a question, answered from that server's FAQ |
| `/serverinfo` | Show server stats |
| `/poll` | Post a quick 👍/👎 poll |
| `/remind` | Get DM'd a reminder after N minutes (up to one week) |

---
## Tech Stack
| Tool | Purpose |
|---|---|
| `discord.py` | Bot framework, slash commands, background tasks |
| `google-genai` | Gemini embeddings for the FAQ (free tier) |
| `groq` | LLM answers for the FAQ (free tier) |
| `numpy` | Cosine similarity search |
| `sqlite3` | Settings, warnings, deadlines, respect points (built into Python) |
| `python-dotenv` | Environment variable loading |
| `pytest` + `ruff` + GitHub Actions | Tests and lint on every push |
| Docker | Containerized, portable deployment |

---
## Hosting for Free
A Discord bot has to stay connected all the time, so hosts that "sleep" when
idle don't work. Hearth itself uses about 80 MB of RAM with every feature
loaded (measured before connecting; a busy server adds a little for Discord's
member cache and the FAQ vectors), so plan on roughly 100–150 MB. With the
operating system and Docker on top, a server with **1 GB of RAM** is plenty —
for example Oracle Cloud's Always Free tier, or an old laptop or Raspberry Pi
at home.

On a server, run it in the background and have Docker restart it after crashes
and reboots:
```bash
docker run -d --restart unless-stopped --env-file .env -v $(pwd)/data:/app/data --name hearth hearth
```

---
## Use Case
Built as a reference implementation for common Discord bot use cases —
onboarding, community management, moderation, study groups and support —
rather than a single-purpose bot. A server admin can drop Hearth into their own
server and set everything up from inside Discord, no code changes required.

---
## Known Limitations
- The FAQ retrieves a handful of relevant *chunks*, not the whole knowledge base at once. That makes it strong at specific factual questions but unable to answer whole-document questions like "how many FAQ entries are there?" — the bot says it doesn't have that information rather than guess.
- `/remind` reminders are held in memory, so one still waiting when the bot restarts is lost (`/deadline` reminders are saved).
- FAQs loaded before the switch to Gemini embeddings need to be loaded again with `/faqload`; the bot says so when someone asks.

---
## Author
**Manish Pandeya** · [github.com/maniesh-lab](https://github.com/maniesh-lab)
