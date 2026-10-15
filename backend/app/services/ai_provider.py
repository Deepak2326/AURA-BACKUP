"""AI generation service with provider interface.

Supports:
- mock: Deterministic provider for tests and local demos (no external deps).
- local: Local Ollama/HuggingFace model.
- grok: Grok API.

The mock provider generates a realistic CRM configuration from keywords
in the prompt. It is NOT a real LLM and is clearly labeled as such.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.schemas.crm import CRMConfiguration, CRMField, CRMEntity
from app.services.laya_system1 import LayaSystem1


class AIProvider(ABC):
    """Abstract AI provider interface."""

    @abstractmethod
    def generate_crm_config(self, prompt: str) -> dict[str, Any]:
        """Generate a CRM configuration dict from a natural-language prompt."""
        ...

    @abstractmethod
    def generate_evolution_proposal(
        self, prompt: str, crm_config: dict[str, Any], analytics: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate an evolution proposal based on CRM state and analytics."""
        ...


class MockAIProvider(AIProvider):
    """Deterministic mock provider using Laya System 1 decision intelligence."""

    def generate_crm_config(self, prompt: str) -> dict[str, Any]:
        """Generate a mock CRM configuration using Laya System 1 scaffolding."""
        return LayaSystem1.generate_scaffold_config(prompt)

    def generate_evolution_proposal(
        self, prompt: str, crm_config: dict[str, Any], analytics: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate an evolution proposal using Laya System 1 allowlisted rules."""
        return LayaSystem1.generate_scaffold_proposal(prompt, crm_config, analytics)


def get_ai_provider(provider_name: str = "mock") -> AIProvider:
    """Factory function to get the configured AI provider."""
    normalized = (provider_name or "mock").lower().strip()
    if normalized == "mock":
        return MockAIProvider()
    if normalized in ("local", "ollama"):
        from app.config import get_settings
        from app.services.laya_runtime import LayaDecisionEngine
        from app.services.ollama_provider import OllamaAIProvider

        settings = get_settings()
        return OllamaAIProvider(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model or "qwen3:4b",
            enable_fallback=True,
            decision_engine=LayaDecisionEngine(),
        )
    raise ValueError(f"Unsupported AI provider: {provider_name}. Use 'mock' or 'local' for demos.")

