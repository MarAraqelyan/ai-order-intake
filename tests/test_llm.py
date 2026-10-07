"""
Tests for src/llm.py: tool loop, failure categories, recordings, and replay matching.

TEST DOUBLE NOTE: FakeOpenAI below is a scripted stand-in for the OpenAI client.
Nothing here calls the real API, and recordings written by these tests go to a
temporary directory - they are never real-call recordings.
"""
import json
from types import SimpleNamespace

import openai
import pytest

from src import llm
from src.llm import LLMProcessingError

R1_TEXT = "Please send 2 individual CAB-1 cables."
GOOD_FINAL = json.dumps({
    "status": "draft",
    "lines": [{"sku": "CAB-1", "quantity": 2, "source_quote": "2 individual CAB-1 cables",
               "is_unresolved": False, "clarification_reason": None}],
    "clarification_message": None,
})


def tool_call_response(arguments, name="search_catalog", call_id="call_fake_1"):
    tc = SimpleNamespace(id=call_id, type="function",
                         function=SimpleNamespace(name=name, arguments=arguments))
    return _response("tool_calls", SimpleNamespace(content=None, tool_calls=[tc], refusal=None))


def final_response(content, finish_reason="stop", refusal=None):
    return _response(finish_reason, SimpleNamespace(content=content, tool_calls=None, refusal=refusal))


def _response(finish_reason, message):
    return SimpleNamespace(id="fake-response", model="fake-model-for-tests", usage=None,
                           choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])


@pytest.fixture
def fake_openai(monkeypatch):
    """Install a FakeOpenAI client that returns the scripted responses in order."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-key")
    script = []
    calls = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            calls.append(("create", kwargs))
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return SimpleNamespace(script=script, calls=calls)


def run_live(tmp_path, request_id="R1", text=R1_TEXT, model="gpt-4.1-mini"):
    return llm.call_llm(request_id, text, mode="live", model=model,
                        recordings_dir=tmp_path, order_ref="O1")


class TestLiveToolLoop:

    def test_success_records_and_replays(self, fake_openai, tmp_path, monkeypatch):
        fake_openai.script += [tool_call_response('{"query": "CAB-1"}'), final_response(GOOD_FINAL)]
        result = run_live(tmp_path)

        assert result["model"] == "fake-model-for-tests"   # model id reported by the API
        assert result["requested_model"] == "gpt-4.1-mini"
        assert result["tool_calls"][0]["result"]["sku"] == "CAB-1"
        assert result["draft"].lines[0].quantity == 2

        record = json.loads((tmp_path / result["recording_file"]).read_text())
        assert record["recording_version"] == 2
        assert record["fingerprint_components"]["model"] == "gpt-4.1-mini"
        assert [r["finish_reason"] for r in record["rounds"]] == ["tool_calls", "stop"]
        assert "test-placeholder" not in json.dumps(record)  # no key in recordings

        # Settings sent to the API.
        create_kwargs = [c[1] for c in fake_openai.calls if c[0] == "create"][0]
        assert create_kwargs["response_format"]["json_schema"]["strict"] is True
        assert create_kwargs["tools"][0]["function"]["strict"] is True
        client_kwargs = [c[1] for c in fake_openai.calls if c[0] == "client"][0]
        assert client_kwargs == {"timeout": llm.REQUEST_TIMEOUT_SECONDS,
                                 "max_retries": llm.MAX_RETRIES}

        # Replay without a key returns the same answer.
        monkeypatch.delenv("OPENAI_API_KEY")
        replayed = llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path,
                                order_ref="O1")
        assert replayed["mode"] == "replay"
        assert replayed["final_response"] == GOOD_FINAL
        assert replayed["raw_response"]["replayed"] is True

    @pytest.mark.parametrize("response, category", [
        (final_response(None, refusal="I can't help with that."), "refusal"),
        (final_response(None, finish_reason="content_filter"), "refusal"),
        (final_response('{"status": "dr', finish_reason="length"), "incomplete"),
        (final_response(""), "missing-output"),
        (final_response("not json"), "invalid-output"),
        (final_response('{"status": "draft", "lines": []}'), "invalid-output"),
    ])
    def test_bad_final_answers_fail_visibly(self, fake_openai, tmp_path, response, category):
        fake_openai.script += [response]
        with pytest.raises(LLMProcessingError, match=rf"\[{category}\]"):
            run_live(tmp_path)

    def test_failed_call_is_recorded_and_replays_as_failure(self, fake_openai, tmp_path):
        fake_openai.script += [final_response('{"status": "dr', finish_reason="length")]
        with pytest.raises(LLMProcessingError):
            run_live(tmp_path)
        with pytest.raises(LLMProcessingError, match=r"\[incomplete\].*\(replayed\)"):
            llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path, order_ref="O1")

    @pytest.mark.parametrize("arguments", [
        "not json", "{}", '{"query": ""}', '{"query": "   "}', '{"query": 5}',
        '{"query": "CAB-1", "limit": 99}', '{"query": "' + "x" * 101 + '"}',
    ])
    def test_invalid_tool_arguments_fail(self, fake_openai, tmp_path, arguments):
        fake_openai.script += [tool_call_response(arguments)]
        with pytest.raises(LLMProcessingError, match=r"\[invalid-tool-arguments\]"):
            run_live(tmp_path)

    def test_unknown_tool_fails(self, fake_openai, tmp_path):
        fake_openai.script += [tool_call_response('{"query": "x"}', name="delete_orders")]
        with pytest.raises(LLMProcessingError, match=r"\[invalid-tool-arguments\]"):
            run_live(tmp_path)

    def test_tool_round_limit(self, fake_openai, tmp_path):
        fake_openai.script += [tool_call_response('{"query": "CAB-1"}')
                               for _ in range(llm.MAX_TOOL_ROUNDS)]
        with pytest.raises(LLMProcessingError, match=r"\[tool-round-limit\]"):
            run_live(tmp_path)

    def test_api_errors_are_processing_failures(self, fake_openai, tmp_path):
        fake_openai.script += [openai.APIConnectionError(request=None)]
        with pytest.raises(LLMProcessingError, match=r"\[api-error\] APIConnectionError"):
            run_live(tmp_path)
        assert list(tmp_path.glob("*.json")) == []  # nothing to record without a response

    def test_live_without_key_fails_clearly(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(LLMProcessingError, match="OPENAI_API_KEY is not set"):
            run_live(tmp_path)


class TestReplayMatching:

    def _record(self, fake_openai, tmp_path):
        fake_openai.script += [tool_call_response('{"query": "CAB-1"}'), final_response(GOOD_FINAL)]
        return run_live(tmp_path)

    def test_missing_recording(self, tmp_path):
        with pytest.raises(LLMProcessingError, match=r"\[replay-missing\].*R1"):
            llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path, order_ref="O1")

    def test_model_change_is_a_mismatch(self, fake_openai, tmp_path):
        self._record(fake_openai, tmp_path)
        with pytest.raises(LLMProcessingError, match=r"\[replay-mismatch\].*differs in model"):
            llm.call_llm("R1", R1_TEXT, mode="replay", model="gpt-other",
                         recordings_dir=tmp_path, order_ref="O1")

    def test_prompt_change_is_a_mismatch(self, fake_openai, tmp_path, monkeypatch):
        self._record(fake_openai, tmp_path)
        monkeypatch.setattr(llm, "SYSTEM_PROMPT", llm.SYSTEM_PROMPT + "\nNew rule.")
        with pytest.raises(LLMProcessingError, match=r"differs in system_prompt_sha256"):
            llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path, order_ref="O1")

    def test_catalog_change_is_a_mismatch(self, fake_openai, tmp_path, monkeypatch):
        self._record(fake_openai, tmp_path)
        # TEST-ONLY catalog copy with a changed price; the supplied catalog is untouched.
        test_catalog = tmp_path / "test-only-catalog.json"
        test_catalog.write_text(json.dumps([{"sku": "CAB-1", "description": "USB-C cable 1 m",
                                             "unit_cents": 2001}]))
        original = llm.fingerprint_components
        monkeypatch.setattr(llm, "fingerprint_components",
                            lambda *a, **k: original(*a, catalog_path=test_catalog))
        with pytest.raises(LLMProcessingError, match=r"differs in catalog_sha256"):
            llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path, order_ref="O1")

    def test_text_change_is_a_mismatch(self, fake_openai, tmp_path):
        self._record(fake_openai, tmp_path)
        with pytest.raises(LLMProcessingError, match=r"differs in user_message_sha256"):
            llm.call_llm("R1", "Please send 3 CAB-1 cables.", mode="replay",
                         recordings_dir=tmp_path, order_ref="O1")

    def test_legacy_recording_is_reported(self, tmp_path):
        (tmp_path / "R1_143c2ca5.json").write_text(json.dumps({"recording_version": 1}))
        with pytest.raises(LLMProcessingError, match="legacy format"):
            llm.call_llm("R1", R1_TEXT, mode="replay", recordings_dir=tmp_path, order_ref="O1")
