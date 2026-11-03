"""Objects shared by all requests, built once at startup (tests build their own)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request

from app.agent.claude_client import ClaudeClient, MessageCreator
from app.agent.ollama_client import OllamaClient
from app.config import Settings
from app.services.bridge import AppsScriptBridge, Bridge
from app.services.common import ServiceContext
from app.sessions import SessionStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Container:
    settings: Settings
    bridge: Bridge
    model: MessageCreator  # OllamaClient or ClaudeClient, chosen by LLM_PROVIDER
    sessions: SessionStore
    now: Callable[[], datetime] = utc_now

    @property
    def service_context(self) -> ServiceContext:
        return ServiceContext(settings=self.settings, bridge=self.bridge, now=self.now)

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        return cls(
            settings=settings,
            bridge=AppsScriptBridge(settings),
            model=OllamaClient(settings) if settings.llm_provider == "ollama" else ClaudeClient(settings),
            sessions=SessionStore(settings.session_idle_ttl_seconds, utc_now),
        )


def get_container(request: Request) -> Container:
    return request.app.state.container
