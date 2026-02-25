"""Azure OpenAI configuration — loads env vars and builds authenticated client.

Uses Azure CLI credential (az login must be done once).
Set the following in your .env:
  AZURE_OPENAI_ENDPOINT          — e.g. https://my-resource.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT_NAME   — chat completion deployment (e.g. gpt-4o)
  AZURE_OPENAI_API_VERSION       — e.g. 2024-08-01-preview
  AZURE_OPENAI_MODEL_NAME        — model display name (e.g. gpt-4o)
  AZURE_OPENAI_REALTIME_DEPLOYMENT — realtime deployment (e.g. gpt-4o-realtime-preview)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import AzureOpenAI


@dataclass
class AzureOpenAIConfig:
    endpoint: str
    deployment: str
    api_version: str
    model_name: str
    realtime_deployment: str


def load_config() -> AzureOpenAIConfig:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    model_name = os.getenv("AZURE_OPENAI_MODEL_NAME", deployment)
    realtime_deployment = os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT", "gpt-4o-realtime-preview")

    missing = [k for k, v in {
        "AZURE_OPENAI_ENDPOINT": endpoint,
        "AZURE_OPENAI_DEPLOYMENT_NAME": deployment,
    }.items() if not v]

    if missing:
        raise ValueError(f"Missing env vars: {missing}. Check your .env")

    return AzureOpenAIConfig(
        endpoint=endpoint,
        deployment=deployment,
        api_version=api_version,
        model_name=model_name,
        realtime_deployment=realtime_deployment,
    )


def build_client(cfg: AzureOpenAIConfig) -> AzureOpenAI:
    """Build an AzureOpenAI client authenticated via Azure CLI (az login)."""
    token_provider = get_bearer_token_provider(
        AzureCliCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        api_version=cfg.api_version,
        azure_endpoint=cfg.endpoint,
        azure_ad_token_provider=token_provider,
    )


def is_azure_configured() -> bool:
    """Return True if Azure OpenAI env vars are present."""
    return bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"))


def get_realtime_wss_url(cfg: AzureOpenAIConfig) -> str:
    """Build the Azure OpenAI Realtime WebSocket URL from the endpoint."""
    # Convert https:// → wss://
    wss_base = cfg.endpoint.replace("https://", "wss://").replace("http://", "ws://")
    return (
        f"{wss_base}/openai/realtime"
        f"?api-version={cfg.api_version}"
        f"&deployment={cfg.realtime_deployment}"
    )
