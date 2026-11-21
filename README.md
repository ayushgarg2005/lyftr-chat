# Resilient LLM Chat

A small full-stack chatbot: React frontend, FastAPI backend, LiteLLM for model calls and caching, SQLite for chat history. If an API key is rate-limited or the provider errors out, the request moves on to the next key in the pool, and the user never sees a broken screen. Repeated prompts are served from LiteLLM's cache.

Every assistant reply in the UI shows where it came from (`LIVE_INFERENCE` / `LLM_CACHE`), the model, server latency, and the input/output tokens for that request.

```
/backend     FastAPI app, LiteLLM key pool, SQLite storage, pytest suite
/frontend    React (Vite) app, served by nginx in Docker
/scripts     verify.sh - curl walkthrough of the whole API contract
docker-compose.yml, Makefile, .env.example
```

## 1. Live URLs

- Frontend: `<add your frontend URL>`
- Backend: `<add your backend URL>` (try `/health`)
- Demo video: `<add link>`

## 2. Run locally

You need at least two API keys for any provider LiteLLM supports. I used Gemini keys from Google AI Studio. Note that keys from the same Google Cloud project share one quota, so make the second key in a different project if you want real failover.

### With Docker

```bash
cp .env.example .env        # then put your keys in LLM_API_KEYS
make up                     # builds and starts both containers
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000 (health check at http://localhost:8000/health)

`make down` stops everything. `make logs` tails the logs. `make clean` also deletes the volume (history and cache). If `.env` is missing, `make up` copies it from `.env.example` first.

The SQLite file is at `/data/app.db` inside the `chat-data` named volume, so history survives `make down && make up`.

### Without Docker

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-one,key-two
export DATABASE_URL=sqlite:///./app.db CACHE_DIR=./.llm_cache
export ALLOWED_ORIGINS=http://localhost:3000 ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
```

Tests (these don't need real keys or network, the provider is faked underneath LiteLLM):

```bash
cd backend && python -m pytest -q
```

Frontend (Node 18+):

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev     # http://localhost:3000
```

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `LLM_API_KEYS` | (required) | Comma-separated pool. Ids are `key_1`, `key_2`, ... in order |
| `LLM_MODEL` | `gemini/gemini-2.5-flash` | Any LiteLLM model string |
| `LLM_MODELS` | empty | Optional, one model per key (e.g. mixing providers) |
| `DATABASE_URL` | `sqlite:///./app.db` | `sqlite:////data/app.db` in Docker |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `false` | Turns on the `X-Simulate-Failure` header |
| `CACHE_TYPE` | `disk` | `disk`, `local` (in-memory) or `off` |
| `CACHE_DIR` | `./.llm_cache` | Disk cache location (`/data/llm_cache` in Docker) |
| `CACHE_TTL_SECONDS` | `86400` | Cache entry lifetime |
| `DEFAULT_SYSTEM_PROMPT` | short helpful-assistant prompt | Used when a session has none |
| `LLM_TIMEOUT_SECONDS` | `30` | Per-key timeout, a timeout also triggers failover |
| `MAX_HISTORY_MESSAGES` | `40` | How many past messages are sent to the model |
| `API_URL` / `FRONTEND_API_URL` | `http://localhost:8000` | Backend URL the browser calls (Docker, read at container start) |
| `VITE_API_URL` | `http://localhost:8000` | Same thing, but baked in at build time (Vercel/Netlify) |

### Deploying

What I did:

- **Backend on Render**: New Web Service from the repo, root directory `backend`, runtime Docker. Set `LLM_API_KEYS`, `ALLOWED_ORIGINS=<frontend url>`, `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL=sqlite:////data/app.db`. Render passes `PORT` and the container listens on it. To keep history across redeploys, attach a disk mounted at `/data` (Railway volumes work the same way).
- **Frontend on Vercel**: import the repo, root directory `frontend`, framework Vite, env `VITE_API_URL=<backend url>`.

## 3. Design decisions

### Key fallback

The key pool (`backend/app/llm.py`) is an ordered list: `key_1`, `key_2`, ... Each request starts at `key_1` and walks down the list. Every attempt is a `litellm.acompletion` call with that key and `num_retries=0`, because retrying is the pool's job and I didn't want the SDK quietly retrying the same rate-limited key.

Failover happens on any provider error: LiteLLM maps provider responses to typed exceptions (`RateLimitError` for 429/quota, `InternalServerError`, `BadGatewayError`, `ServiceUnavailableError`, timeouts, connection errors), and all of them send us to the next key. Revoked or invalid keys also fail over, since a dead key shouldn't take the app down. An empty completion counts as a failure too. If every key fails, the API returns `503 {"detail": "all providers unavailable"}` and nothing is written to the history.

I looked at LiteLLM's `Router` with `fallbacks`, which does something similar. I went with an explicit loop over plain LiteLLM calls because the contract needs exact `key_id` and `fallback_attempts` values per response and a per-request failure hook. Both are easy to get right when the order is deterministic, and awkward to pull out of the Router's internal retry and cooldown logic. LiteLLM still does the provider calls, the error mapping and the caching.

`X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) is parsed into `{key_id: status}`. When the loop reaches a listed key, it raises the same LiteLLM exception that a real 429/500/502/503 would produce, so simulated and real failures go through exactly the same path. A simulated request skips the cache *read*. Otherwise a previously cached prompt would come back as `LLM_CACHE` with `key_id: null` and you couldn't see failover at all. The reply is still written to the cache. Unknown keys or statuses in the header are ignored.

Keys never leave the backend. Responses and logs only mention `key_1`, `key_2`, ...

### Caching

LiteLLM's built-in cache is turned on globally (`litellm.cache = Cache(type="disk")`, or `local` for in-memory). Its cache key is built from the model and the full message list (system prompt + history + new message) plus sampling params. The API key is not part of it, so a reply produced by `key_2` can be served later even when `key_1` is healthy. This gives the behavior the spec asks for:

- the same first message in a new session is a hit (same system prompt + same single user message),
- a different message, a different system prompt or a different history is always a miss.

A hit is detected via `response._hidden_params["cache_hit"]`. Hits return the stored text and the stored usage, so the token numbers match the original call, with `key_id: null` and `fallback_attempts: 0`. Hits skip the network, so they come back in a few ms.

One thing I hit while testing: LiteLLM writes to the cache from a background success callback, so an identical request fired immediately afterwards could still miss. After a live response I also write it to the cache directly (`litellm.cache.async_add_cache`) so the second request is reliably a hit.

I use the disk cache by default and keep it in the same `/data` volume as the database, so it survives restarts too. No Redis, per the requirements.

I pinned `litellm==1.82.6`. Newer releases I tried pulled proxy-only dependencies into the cache-hit code path and crashed on a cache hit without the `litellm[proxy]` extras.

### SQLite session storage

`backend/app/db.py`. The schema is created on startup (`CREATE TABLE IF NOT EXISTS`):

- `messages`: exactly the table from the spec, plus an index on `(session_id, id)`.
- `sessions`: session id, the session's system prompt, timestamps. This is what lets the optional system prompt "stick" to a session without being stored as a message, and it keeps it out of `GET /sessions/{id}/messages`.

The client only sends the new message. The backend loads the session's history (last `MAX_HISTORY_MESSAGES`), adds the system prompt and the new message, and sends that list to the model. The user message and the assistant reply are inserted in one transaction *after* the model answers, so a 422 or 503 leaves no half-written turn behind. The connection runs in WAL mode, and each operation opens a short-lived connection, which is plenty for this load.

Assistant rows store the full response metadata as JSON, so the history endpoint returns `resolution_source`, tokens, model, latency, etc. for every past reply.

### Other details

- Validation is done with Pydantic: `session_id` 1-128 chars and not blank, `message` 1-4000 chars after trimming. Wrong types and malformed JSON get FastAPI's default 422 body.
- A catch-all exception handler returns `{"detail": "internal server error"}`, so there are never stack traces or HTML error pages.
- `latency_ms` is measured on the server with `time.perf_counter()` from the start of the request handler until the reply is ready.
- `input_tokens` / `output_tokens` come from the provider's `usage` for that single call. If a provider doesn't return usage, LiteLLM's tokenizer counts them instead.

## 4. Verification

With the stack running, `./scripts/verify.sh` (or `./scripts/verify.sh https://your-backend-url`) runs through every case below with curl.

**Cache hit**: open the app, send a message, then reload the page (new session) and send the exact same message. The first reply shows `LIVE_INFERENCE` with a key id, the second one shows `LLM_CACHE`, `no key (cache)`, the same text and tokens, and a much lower latency. With curl:

```bash
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"a1","message":"What is the capital of France?"}'
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"b2","message":"What is the capital of France?"}'
```

**Failover**: in the UI, open Settings and pick a value under "Simulate key failure". The reply's metadata shows `key_2 · 1 fallback`. Choosing "all keys → 429" shows a readable error in the chat. With curl:

```bash
curl -s localhost:8000/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"f1","message":"Name a prime number"}'          # 200, key_id = key_2
curl -s -i localhost:8000/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"f2","message":"hello"}'                        # 503, not stored
```

Real failures look the same in the backend logs: `key_1 failed (429 RateLimitError), trying next key`.

**Token usage per message**: each assistant bubble shows `in X / out Y` for that request only. In a longer conversation `in` grows because the prompt includes more history, but it never adds up earlier requests. Two different first messages of similar length show about the same `in`.

**Multi-turn and history**: tell the bot your name, then ask for it. `GET /sessions/<id>/messages` (the session id is shown in the header of the UI) returns the stored turns, oldest first, with metadata on assistant messages. An unknown id returns `404 {"detail": "session not found"}`.

**Backend tests**: `make test` or `cd backend && python -m pytest -q`. They cover the response shape, every 422 case, cache hit/miss rules, identical reply and tokens on hits, per-request tokens, history and session isolation, simulated and real failover, the 503 path (and that it stores nothing), the disabled simulation switch, and persistence across an app restart.

## 5. Setup Used

- Editor: `<your editor>`
- AI tools: `<the AI tools you used>`
- Tested with Python 3.12/3.13, Node 20/22, Docker Compose v2

## 6. Bonus features

- **Failure simulation in the UI**: a dropdown in Settings sends `X-Simulate-Failure`, so failover can be shown in the demo without curl.
- **Backend status in the header**: the UI calls `/health` on load and shows the key count and cache state, or "backend offline".
- **Disk cache in the volume**: cached replies survive container restarts, like the history does.
- **Per-key models (`LLM_MODELS`)**: the pool can mix models or providers, e.g. a Gemini key with a Groq key as backup.
- **Runtime frontend config**: the frontend image reads `API_URL` when the container starts, so the same image works for any backend without a rebuild.
- **`scripts/verify.sh`**: one command that checks the whole API contract against local or deployed URLs.
- **History limit**: only the last `MAX_HISTORY_MESSAGES` are sent to the model, so long chats don't blow the context window or the token bill.
