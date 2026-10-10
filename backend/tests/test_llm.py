from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from finagent import llm


class Verdict(BaseModel):
    ok: bool
    note: str


def test_available_follows_the_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.available() is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm.available() is False


def test_headers_name_the_workspace_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")
    assert llm._headers() == {"anthropic-workspace-id": "wrkspc_123"}


def test_headers_are_none_without_a_workspace(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    assert llm._headers() is None


def test_model_constants():
    assert llm.SONNET == "claude-sonnet-5-5"
    assert llm.HAIKU == "claude-haiku-5-5"


def test_factories_use_their_model_without_network(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.sonnet().model == llm.SONNET
    assert llm.haiku().model == llm.HAIKU


def test_structured_constrains_the_schema_and_ends_in_the_pydantic_parser():
    parser = PydanticOutputParser(pydantic_object=Verdict)
    chain = llm.structured(ChatAnthropic(model=llm.HAIKU, api_key="sk-test"), parser)
    fmt = chain.first.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "ok" in fmt["schema"]["properties"]
    assert isinstance(chain.last, PydanticOutputParser)
    assert chain.last.pydantic_object is Verdict
    assert chain.last.parse('{"ok": true, "note": "fine"}') == Verdict(ok=True, note="fine")
