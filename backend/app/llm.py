import logging
import os
from dataclasses import dataclass

import litellm
from litellm.caching.caching import Cache

from .config import Settings

log = logging.getLogger("chat.llm")

litellm.suppress_debug_info = True
litellm.drop_params = True

SIMULATED_ERRORS = {
    429: litellm.RateLimitError,
    500: litellm.InternalServerError,
    502: litellm.BadGatewayError,
    503: litellm.ServiceUnavailableError,
}


class AllProvidersFailed(Exception):
    pass


@dataclass
class Key:
    id: str
    secret: str
    model: str


@dataclass
class Completion:
    text: str
    model: str
    key_id: str | None
    from_cache: bool
    input_tokens: int
    output_tokens: int
    fallback_attempts: int


def parse_simulation_header(raw: str | None, key_ids: list[str]) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {key_id: status}. Bad entries are ignored."""
    if not raw:
        return {}
    plan: dict[str, int] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        target, _, status = part.partition("=")
        target = target.strip()
        try:
            code = int(status.strip())
        except ValueError:
            continue
        if code not in SIMULATED_ERRORS:
            continue
        if target == "*":
            for key_id in key_ids:
                plan[key_id] = code
        elif target in key_ids:
            plan[target] = code
    return plan


def _simulated_error(key: Key, status: int) -> Exception:
    error_cls = SIMULATED_ERRORS[status]
    return error_cls(
        message=f"simulated {status} for {key.id}",
        llm_provider=key.model.split("/")[0],
        model=key.model,
    )


class KeyPool:
    """Ordered pool of API keys. Each request walks the pool from key_1 until one answers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.keys = [
            Key(id=f"key_{i + 1}", secret=secret, model=settings.models[i])
            for i, secret in enumerate(settings.api_keys)
        ]
        self.cache_enabled = False
        self._setup_cache()

    def _setup_cache(self) -> None:
        if self.settings.cache_type in ("", "none", "off"):
            litellm.cache = None
            return
        if self.settings.cache_type == "disk":
            os.makedirs(self.settings.cache_dir, exist_ok=True)
            litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir, ttl=self.settings.cache_ttl)
        else:
            litellm.cache = Cache(type="local", ttl=self.settings.cache_ttl)
        self.cache_enabled = True

    @property
    def key_ids(self) -> list[str]:
        return [k.id for k in self.keys]

    async def complete(self, messages: list[dict], simulate: dict[str, int] | None = None) -> Completion:
        simulate = simulate or {}
        failures = 0

        for key in self.keys:
            try:
                if key.id in simulate:
                    raise _simulated_error(key, simulate[key.id])
                response = await self._call(key, messages, skip_cache_read=bool(simulate))
            except Exception as exc:  # any provider failure moves us to the next key
                failures += 1
                status = getattr(exc, "status_code", None)
                log.warning("%s failed (%s %s), trying next key", key.id, status, type(exc).__name__)
                continue

            from_cache = bool(response._hidden_params.get("cache_hit"))
            text = (response.choices[0].message.content or "").strip()
            if not text:
                failures += 1
                log.warning("%s returned an empty reply, trying next key", key.id)
                continue

            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            if input_tokens is None:
                input_tokens = litellm.token_counter(model=key.model, messages=messages)
            if output_tokens is None:
                output_tokens = litellm.token_counter(model=key.model, text=text)

            if not from_cache and self.cache_enabled:
                # LiteLLM writes the cache from a background callback. Writing it here as
                # well means an identical request sent right after this one is a hit.
                await litellm.cache.async_add_cache(response, model=key.model, messages=messages)

            return Completion(
                text=text,
                model=key.model,
                key_id=None if from_cache else key.id,
                from_cache=from_cache,
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                fallback_attempts=0 if from_cache else failures,
            )

        raise AllProvidersFailed()

    async def _call(self, key: Key, messages: list[dict], skip_cache_read: bool):
        kwargs = dict(
            model=key.model,
            messages=messages,
            api_key=key.secret,
            timeout=self.settings.request_timeout,
            num_retries=0,  # retrying is the pool's job, not the SDK's
        )
        if self.cache_enabled:
            kwargs["caching"] = True
            if skip_cache_read:
                # Simulated runs must hit a (fake-failing) provider, so don't serve them from cache.
                kwargs["cache"] = {"no-cache": True}
        return await litellm.acompletion(**kwargs)
