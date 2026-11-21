import litellm
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeProvider:
    """Stands in for the real provider underneath litellm.acompletion.

    It still runs through LiteLLM (mock_response), so LiteLLM's cache and error
    types are exercised for real. Keys listed in `fail` raise the given status.
    """

    def __init__(self, original):
        self.original = original
        self.calls = []
        self.fail: dict[str, int] = {}

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        status = self.fail.get(kwargs["api_key"])
        if status == 429:
            kwargs["mock_response"] = "litellm.RateLimitError"
        elif status:
            kwargs["mock_response"] = "litellm.InternalServerError"
        else:
            messages = kwargs["messages"]
            turns = sum(1 for m in messages if m["role"] == "user")
            text = f"reply to '{messages[-1]['content'][:50]}' (turn {turns})"
            prompt_tokens = litellm.token_counter(model="gpt-4o", messages=messages)
            completion_tokens = litellm.token_counter(model="gpt-4o", text=text)
            kwargs["mock_response"] = {
                "model": kwargs["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        return await self.original(**kwargs)


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider(litellm.acompletion)
    monkeypatch.setattr(litellm, "acompletion", fake)
    return fake


@pytest.fixture
def make_client(tmp_path, provider):
    clients = []

    def _make(**overrides):
        settings = Settings(
            api_keys=["secret-one", "secret-two"],
            models=["gemini/gemini-2.5-flash", "gemini/gemini-2.5-flash"],
            database_url=f"sqlite:///{tmp_path}/test.db",
            enable_failure_simulation=True,
            cache_type="local",
            cache_dir=str(tmp_path / "cache"),
        )
        for name, value in overrides.items():
            setattr(settings, name, value)
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()
