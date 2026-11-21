import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .db import Database
from .llm import AllProvidersFailed, KeyPool, parse_simulation_header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chat")
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str
    system_prompt: str | None = None

    @field_validator("session_id")
    @classmethod
    def session_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be blank")
        return value

    @field_validator("message")
    @classmethod
    def message_length(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        if len(value) > 4000:
            raise ValueError("message must be at most 4000 characters")
        return value

    @field_validator("system_prompt")
    @classmethod
    def clean_system_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(settings.db_path)
        app.state.db.init()
        app.state.pool = KeyPool(settings)
        log.info(
            "ready: %d key(s), models=%s, cache=%s, failure simulation=%s",
            len(app.state.pool.keys),
            sorted(set(settings.models)),
            settings.cache_type,
            settings.enable_failure_simulation,
        )
        yield

    app = FastAPI(title="Resilient LLM Chat", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    async def health(request: Request):
        pool: KeyPool = request.app.state.pool
        db: Database = request.app.state.db
        body = {"status": "ok", "keys_configured": len(pool.keys), "cache_enabled": pool.cache_enabled}
        if not db.ping():
            return JSONResponse(status_code=503, content={**body, "status": "error", "detail": "database unreachable"})
        if not pool.keys:
            return JSONResponse(status_code=503, content={**body, "status": "error", "detail": "no API keys configured"})
        return body

    @app.post("/chat")
    async def chat(
        body: ChatRequest,
        request: Request,
        x_simulate_failure: str | None = Header(default=None),
    ):
        started = time.perf_counter()
        pool: KeyPool = request.app.state.pool
        db: Database = request.app.state.db

        session = db.get_session(body.session_id)
        system_prompt = body.system_prompt or (session["system_prompt"] if session else None)
        system_prompt = system_prompt or settings.default_system_prompt

        history = db.get_history(body.session_id, limit=settings.max_history_messages)
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": body.message})

        simulate = {}
        if settings.enable_failure_simulation:
            simulate = parse_simulation_header(x_simulate_failure, pool.key_ids)

        try:
            result = await pool.complete(messages, simulate=simulate)
        except AllProvidersFailed:
            return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

        metadata = {
            "resolution_source": "LLM_CACHE" if result.from_cache else "LIVE_INFERENCE",
            "model": result.model,
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }

        db.save_exchange(body.session_id, body.message, result.text, metadata, body.system_prompt)

        return {"session_id": body.session_id, "reply": result.text, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str, request: Request):
        db: Database = request.app.state.db
        messages = db.get_history(session_id)
        if not messages:
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
