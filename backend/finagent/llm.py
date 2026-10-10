"""Model factories. Sonnet runs agents and reads PDFs; Haiku categorizes Rows in bulk."""

import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import PydanticOutputParser

load_dotenv()

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _headers() -> dict[str, str] | None:
    """Keys not scoped to a workspace must name one on every request."""
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID")
    return {"anthropic-workspace-id": ws} if ws else None


def sonnet(effort: str = "medium") -> ChatAnthropic:
    return ChatAnthropic(model=SONNET, max_tokens=16000, effort=effort, default_headers=_headers())


def haiku() -> ChatAnthropic:
    return ChatAnthropic(model=HAIKU, max_tokens=8000, effort="low", default_headers=_headers())


def structured(model: ChatAnthropic, parser: PydanticOutputParser):
    """model constrained to the parser's JSON schema, then validated by a PydanticOutputParser."""
    return model.with_structured_output(parser.pydantic_object, method="json_schema")
