"""Decode compressed embedding responses through the production vector stack."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import brotli
import httpx2
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.providers.openai import OpenAIProvider

from core.settings.config_editor import upsert_model_mapping
from core.vector import VectorService
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class EmbeddingCompressedResponseScenario(BaseScenario):
    @with_local_user_authority
    async def test_scenario(self):
        await self.start_system()
        alias = "compressed_embedding_fixture"
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            assert request.url.path == "/v1/embeddings"
            body = json.dumps(
                {
                    "object": "list",
                    "model": "text-embedding-3-small",
                    "data": [
                        {
                            "object": "embedding",
                            "index": index,
                            "embedding": [0.25, 0.75],
                        }
                        for index, _ in enumerate(payload["input"])
                    ],
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                }
            ).encode()
            # A byte stream exercises decoding during SDK response consumption,
            # rather than handing the SDK an already-decoded fake JSON object.
            return httpx2.Response(
                200,
                headers={"content-type": "application/json", "content-encoding": "br"},
                stream=httpx2.ByteStream(brotli.compress(body)),
            )

        try:
            upsert_model_mapping(
                name=alias,
                provider="openai",
                model_string="text-embedding-3-small",
                capabilities=["embedding"],
                dimensions=2,
            )
            async with httpx2.AsyncClient(
                transport=httpx2.MockTransport(respond)
            ) as client:
                model = OpenAIEmbeddingModel(
                    "text-embedding-3-small",
                    provider=OpenAIProvider(
                        api_key="fixture-not-a-secret", http_client=client
                    ),
                )
                service = VectorService(embedding_model_overrides={alias: model})
                documents = await service.embed_documents(
                    ["first", "second"], model_alias=alias
                )
                query = await service.embed_query("query", model_alias=alias)
            assert len(documents.vectors) == 2 and len(query.vectors) == 1
            assert all(vector.vector == (0.25, 0.75) for vector in documents.vectors)
            assert query.vectors[0].vector == (0.25, 0.75)
            assert documents.dimensions == query.dimensions == 2
            assert [payload["input"] for payload in requests] == [
                ["first", "second"],
                ["query"],
            ]
        finally:
            await self.stop_system()
            self.teardown_scenario()
