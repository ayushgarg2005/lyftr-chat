import uuid


def new_session() -> str:
    return f"s-{uuid.uuid4().hex[:8]}"


def send(client, message, session_id=None, headers=None, **extra):
    payload = {"session_id": session_id or new_session(), "message": message, **extra}
    return client.post("/chat", json=payload, headers=headers or {})


# --- health -----------------------------------------------------------------

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_health_without_keys(make_client):
    client = make_client(api_keys=[], models=[])
    res = client.get("/health")
    assert res.status_code == 503
    assert "detail" in res.json()


# --- /chat basics -----------------------------------------------------------

def test_chat_response_shape(client):
    sid = new_session()
    res = send(client, "What is the capital of France?", sid)
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == sid
    assert body["reply"]
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-2.5-flash"
    assert isinstance(meta["latency_ms"], int)
    assert isinstance(meta["input_tokens"], int) and meta["input_tokens"] > 0
    assert isinstance(meta["output_tokens"], int) and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    assert "secret" not in res.text


def test_message_is_trimmed(client, provider):
    send(client, "   hello there   ")
    assert provider.calls[-1]["messages"][-1]["content"] == "hello there"


# --- validation -------------------------------------------------------------

def test_validation_errors(client):
    cases = [
        {"session_id": "s-1"},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": "s-1", "message": ""},
        {"session_id": "s-1", "message": "     "},
        {"session_id": "s-1", "message": "x" * 4001},
        {"session_id": "x" * 129, "message": "hi"},
        {"session_id": "s-1", "message": 42},
    ]
    for payload in cases:
        res = client.post("/chat", json=payload)
        assert res.status_code == 422, payload
        assert "detail" in res.json()


def test_message_at_limit_is_accepted(client):
    assert send(client, "x" * 4000).status_code == 200


def test_malformed_json(client):
    res = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert "detail" in res.json()


def test_failed_validation_is_not_stored(client):
    sid = new_session()
    client.post("/chat", json={"session_id": sid, "message": ""})
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


# --- caching ----------------------------------------------------------------

def test_same_first_message_in_new_session_hits_cache(client, provider):
    first = send(client, "Tell me a fun fact about owls").json()
    second = send(client, "Tell me a fun fact about owls").json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert second["metadata"]["latency_ms"] <= first["metadata"]["latency_ms"]


def test_different_message_is_never_cached(client):
    send(client, "Tell me about cats")
    res = send(client, "Tell me about dogs").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_different_system_prompt_misses_cache(client):
    send(client, "Say hi", system_prompt="You are a pirate.")
    res = send(client, "Say hi", system_prompt="You are a butler.").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_same_message_later_in_conversation_misses_cache(client):
    send(client, "hello")
    sid = new_session()
    send(client, "something else first", sid)
    res = send(client, "hello", sid).json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request(client):
    a = send(client, "Explain gravity briefly").json()["metadata"]
    b = send(client, "Explain sorting briefly").json()["metadata"]
    assert abs(a["input_tokens"] - b["input_tokens"]) <= 3

    sid = new_session()
    turn1 = send(client, "first question", sid).json()["metadata"]
    turn2 = send(client, "second question", sid).json()["metadata"]
    # more history means a bigger prompt, but not a running total of both requests
    assert turn2["input_tokens"] > turn1["input_tokens"]
    assert turn2["input_tokens"] < turn1["input_tokens"] + turn1["output_tokens"] + turn2["input_tokens"]


# --- multi-turn + history ---------------------------------------------------

def test_history_is_sent_and_stored(client, provider):
    sid = new_session()
    send(client, "My name is Aryan", sid)
    send(client, "What is my name?", sid)

    sent = provider.calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "My name is Aryan"

    res = client.get(f"/sessions/{sid}/messages")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == sid
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert body["messages"][0]["content"] == "My name is Aryan"
    assert "metadata" not in body["messages"][0]
    meta = body["messages"][1]["metadata"]
    assert meta["resolution_source"] in ("LIVE_INFERENCE", "LLM_CACHE")
    assert isinstance(meta["input_tokens"], int)
    assert all(m["role"] != "system" for m in body["messages"])


def test_sessions_are_isolated(client, provider):
    a, b = new_session(), new_session()
    send(client, "secret word is banana", a)
    send(client, "what is the secret word?", b)
    sent = provider.calls[-1]["messages"]
    assert all("banana" not in m["content"] for m in sent)
    assert len(client.get(f"/sessions/{b}/messages").json()["messages"]) == 2


def test_system_prompt_sticks_to_session(client, provider):
    sid = new_session()
    send(client, "hi", sid, system_prompt="Answer like a pirate.")
    send(client, "and again", sid)
    assert provider.calls[-1]["messages"][0] == {"role": "system", "content": "Answer like a pirate."}


def test_unknown_session_404(client):
    res = client.get("/sessions/does-not-exist/messages")
    assert res.status_code == 404
    assert res.json() == {"detail": "session not found"}


def test_history_survives_restart(make_client):
    first = make_client()
    sid = new_session()
    send(first, "remember me", sid)
    second = make_client()
    assert len(second.get(f"/sessions/{sid}/messages").json()["messages"]) == 2


# --- failover ---------------------------------------------------------------

def test_simulated_429_fails_over(client):
    res = send(client, "failover please", headers={"X-Simulate-Failure": "key_1=429"})
    assert res.status_code == 200
    meta = res.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert "simulated" not in res.json()["reply"]


def test_simulated_500_fails_over(client):
    res = send(client, "failover 500", headers={"X-Simulate-Failure": "key_1=500"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_2"


def test_simulation_skips_cache(client):
    send(client, "cached question")
    res = send(client, "cached question", headers={"X-Simulate-Failure": "key_1=429"})
    assert res.json()["metadata"]["key_id"] == "key_2"
    assert res.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_all_keys_fail_returns_503_and_stores_nothing(client):
    sid = new_session()
    res = send(client, "nobody home", sid, headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 503
    assert res.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_both_keys_listed_fail(client):
    res = send(client, "x", headers={"X-Simulate-Failure": "key_1=429,key_2=500"})
    assert res.status_code == 503


def test_simulation_only_for_that_request(client):
    sid = new_session()
    send(client, "one", sid, headers={"X-Simulate-Failure": "key_1=503"})
    res = send(client, "two", sid)
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_simulation_disabled_ignores_header(make_client):
    client = make_client(enable_failure_simulation=False)
    res = send(client, "x", headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_real_provider_errors_fail_over(client, provider):
    provider.fail = {"secret-one": 429}
    res = send(client, "real 429 on key one")
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_2"

    provider.fail = {"secret-one": 500, "secret-two": 429}
    res = send(client, "everything is down")
    assert res.status_code == 503
    assert res.json() == {"detail": "all providers unavailable"}


def test_bad_header_values_are_ignored(client):
    res = send(client, "x", headers={"X-Simulate-Failure": "key_1=418,garbage,key_9=429"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_unknown_route_is_json(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert "detail" in res.json()


def test_disk_cache_survives_restart(make_client):
    first = make_client(cache_type="disk")
    a = send(first, "persist this answer").json()
    second = make_client(cache_type="disk")
    b = send(second, "persist this answer").json()
    assert b["metadata"]["resolution_source"] == "LLM_CACHE"
    assert b["reply"] == a["reply"]
