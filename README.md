# Hive

A multi-agent chat application. Configure AI agents with distinct expertise domains and system prompts, assemble them into conversations, and let them collaborate using a bid-based turn orchestration system with persistent memory.

## What it does

Hive lets you build a panel of AI agents — each with a name, expertise domain, system prompt, communication style, and model — and put them in a conversation together. Rather than round-robin or external scoring, agents independently decide each turn whether they have something to contribute, what kind of contribution it is, and whether the discussion has reached a natural close.

**Core features:**

- **Configurable agents** — per-agent system prompt, expertise domain, communication style, and model (Claude or OpenAI)
- **Bid-based orchestration** — agents bid independently before generating responses; turn types are conveyance, challenge, question, convergence, pass, and backchannel. Conversation ends when agent bids converge on completion, not by an external timer
- **Temporal memory** — episodic and semantic memory with type-aware decay. Mutable state (facts, preferences) ages and can be superseded by newer facts; immutable records (decisions, rejections, events) never expire or decay
- **Shared whiteboard** — persistent per-conversation workspace agents can read and write
- **Proposal system** — agents can propose adding or removing participants mid-conversation; proposals go to a participant vote
- **@mentions** — direct questions to a specific agent
- **Web search** — DuckDuckGo integration as a tool agents can invoke
- **Interrupt / resume** — pause autonomous runs and resume them
- **Debug console** — live view of bid events, memory retrievals, and WebSocket traffic
- **Command palette** — keyboard-accessible navigation

## Architecture

```
frontend/          React 18 + TypeScript + Vite + Tailwind + Zustand
backend/
  app/
    api/routes/    FastAPI route handlers (agents, conversations, chat, websocket, tools, whiteboard)
    models/        SQLAlchemy ORM models
    services/      Business logic (agent, memory, bid, facilitation, embedding, LLM routing)
    config.py      Pydantic settings — all config via .env
  alembic/         Database migrations
  scripts/         Operational utilities (not required for setup)
docs/              Design specs for the temporal memory system
```

**Backend:** FastAPI, SQLAlchemy (async), SQLite via aiosqlite, ChromaDB, Alembic, Anthropic SDK, OpenAI SDK, sentence-transformers, structlog

**Frontend:** React 18, React Router, Zustand, Radix UI, TanStack Virtual, react-markdown, Tailwind CSS

## Requirements

- Python 3.11+
- Node.js 18+
- An Anthropic API key (required) and/or OpenAI API key (needed only for OpenAI-model agents)

## Setup

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then add your API keys
alembic upgrade head
python run.py
```

API runs at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI runs at `http://localhost:5173`.

## Configuration

All backend config is in `backend/.env`. Key variables:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `OPENAI_API_KEY` | — | Optional — needed for OpenAI-model agents |
| `DEFAULT_CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | Default model for new agents |
| `DATABASE_URL` | `sqlite+aiosqlite:///./multi_agent_chat.db` | SQLite path |
| `DEBUG` | `true` | Enables hot reload and SQL echo |

Memory tuning (decay rates, retrieval limits, context window, consolidation threshold) can be adjusted in `config.py` or via env variables — defaults are reasonable for most use cases.

## Limitations

- **No authentication.** Designed for local use. Do not expose publicly without adding auth.
- **SQLite only.** No Postgres support — the async SQLAlchemy setup is compatible but migrations target SQLite.
- **Single-user, single-instance.** No multi-tenancy.
- **Localhost by default.** CORS is set to `localhost:5173`. Adjust `CORS_ORIGINS` in `.env` for other origins.

## License

MIT
