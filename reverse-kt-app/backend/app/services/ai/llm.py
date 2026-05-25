"""OpenAI/Azure OpenAI client helpers: chat completions + embeddings."""

from __future__ import annotations

import json

from openai import AzureOpenAI, OpenAI

from app.core.config import settings


def _chat_client() -> OpenAI | AzureOpenAI:
    ep = settings.azure_openai_endpoint.strip()
    az_key = settings.azure_openai_api_key.strip()
    if ep and az_key:
        return AzureOpenAI(
            azure_endpoint=ep,
            api_key=az_key,
            api_version=settings.azure_openai_api_version,
        )

    oa_key = settings.openai_api_key.strip()
    if not oa_key:
        raise ValueError(
            "OPENAI_API_KEY is missing or empty. Set a real key in the environment (Docker: "
            "compose `environment` or an env file—not `OPENAI_API_KEY=` with no value). "
            "An empty key sends an invalid Authorization header and fails with 'Connection error.'"
        )
    return OpenAI(api_key=oa_key)


def chat_model_name() -> str:
    if settings.azure_openai_endpoint and settings.azure_openai_chat_deployment:
        return settings.azure_openai_chat_deployment
    return settings.openai_model


def embedding_model_name() -> str:
    if settings.azure_openai_endpoint and settings.azure_openai_embedding_deployment:
        return settings.azure_openai_embedding_deployment
    return settings.openai_embedding_model


def chat_json(messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 12000) -> dict:
    client = _chat_client()
    model = chat_model_name()
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    raw = completion.choices[0].message.content or "{}"
    return json.loads(raw)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _chat_client()
    model = embedding_model_name()
    out_vectors: list[list[float]] = []
    batch_size = 64
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        for item in sorted(resp.data, key=lambda d: d.index):
            out_vectors.append(list(item.embedding))
    return out_vectors
