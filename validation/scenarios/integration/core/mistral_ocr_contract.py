"""Validate Mistral OCR URL transport and opt-in enrichment artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import fitz
from pydantic_ai.exceptions import ModelHTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.compaction import _compaction_failure_details
from core.ingestion.import_service import ContentImportService
from core.llm import decision
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario


class MistralOcrContractScenario(BaseScenario):
    """Validate direct PDF OCR without downloading the source through AssistantMD."""

    async def test_scenario(self):
        vault = self.create_vault("MistralOcrContractVault")
        await self.start_system()

        provider_error = ModelHTTPError(
            403, "model", {"message": "Access denied", "code": "denied"}
        )
        assert "Access denied" in str(
            _compaction_failure_details(provider_error, reason="authoring_failed")[
                "error"
            ]
        )
        assert "PRIVATE" not in str(
            _compaction_failure_details(
                RuntimeError("PRIVATE"), reason="authoring_failed"
            )
        )
        with patch.object(decision, "logger") as decision_logger:
            decision._log_decision_failure(
                "alias", "bool", 0.1, provider_error, category="provider"
            )
        decision_failure = decision_logger.warning.call_args.kwargs["data"]
        assert decision_failure["http_status"] == 403
        assert "Access denied" in decision_failure["error"]

        response = Mock(status_code=200)
        response.json.return_value = {
            "model": "mistral-ocr-4-0",
            "usage_info": {"pages_processed": 1},
            "pages": [
                {
                    "index": 0,
                    "markdown": "# Direct OCR",
                    "header": "Report header",
                    "blocks": [{"type": "title", "content": "Direct OCR"}],
                    "confidence_scores": {"average_page_confidence_score": 0.99},
                }
            ],
        }
        with (
            patch(
                "core.ingestion.service.resolve_public_url",
                return_value=("example.org", ["93.184.216.34"]),
            ),
            patch("core.ingestion.service.secret_has_value", return_value=True),
            patch("core.ingestion.capabilities.secret_has_value", return_value=True),
            patch(
                "core.ingestion.strategies.mistral_ocr_common.get_secret_value",
                return_value="test-key",
            ),
            patch(
                "core.ingestion.strategies.mistral_ocr_common.requests.post",
                return_value=response,
            ) as post,
        ):
            metadata_response = self.call_api("/api/metadata")
            pdf_ocr_capability = (
                metadata_response.json()
                .get("ingestion_capabilities", {})
                .get("pdf_ocr", {})
            )
            self.soft_assert_equal(
                pdf_ocr_capability.get("available"),
                True,
                "Metadata should expose backend-derived PDF OCR availability",
            )
            self.soft_assert(
                "blocks" in (pdf_ocr_capability.get("features") or []),
                "Metadata should expose supported PDF OCR enrichments",
            )
            self.soft_assert_equal(
                pdf_ocr_capability.get("default_order"),
                ["pdf_ocr", "pdf_text"],
                "Metadata should expose the configured PDF strategy order",
            )
            service = ContentImportService(str(vault))
            submitted = service.submit(
                sources="https://example.org/report.pdf",
                options={
                    "strategies": ["pdf_ocr"],
                    "include_ocr_blocks": True,
                    "extract_ocr_header": True,
                    "ocr_confidence": "page",
                },
            )
            await get_runtime_context().ingestion_worker.run_once()
            result = service.status(job_ids=submitted[0].job_id)[0]

        self.soft_assert_equal(result.status, "completed", "OCR job should complete")
        self.soft_assert_equal(
            result.selected_strategy, "pdf_ocr", "PDF OCR should be selected"
        )
        self.soft_assert_equal(
            result.selected_provider, "mistral", "Provider should be recorded"
        )
        request_payload = json.loads(post.call_args.kwargs["data"])
        self.soft_assert_equal(
            request_payload["document"],
            {
                "type": "document_url",
                "document_url": "https://example.org/report.pdf",
            },
            "Public OCR-only PDF URLs should be delivered directly to Mistral",
        )
        self.soft_assert_equal(
            request_payload.get("include_blocks"), True, "Blocks should be opt-in"
        )
        self.soft_assert_equal(
            request_payload.get("extract_header"),
            True,
            "Header extraction should be opt-in",
        )
        self.soft_assert_equal(
            request_payload.get("confidence_scores_granularity"),
            "page",
            "Confidence granularity should be forwarded",
        )
        outputs = result.outputs
        self.soft_assert(
            any(path.endswith("/ocr.json") for path in outputs),
            "Requested structured OCR data should be retained as an asset",
        )

        local_pdf = vault / "local-fallback.pdf"
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Local extraction fallback")
            document.save(local_pdf)
        with patch("core.ingestion.service.secret_has_value", return_value=False):
            fallback_submission = service.submit(sources="local-fallback.pdf")
            await get_runtime_context().ingestion_worker.run_once()
            fallback_result = service.status(job_ids=fallback_submission[0].job_id)[0]

        self.soft_assert_equal(
            fallback_result.status,
            "completed",
            "Missing Mistral credentials should not prevent local PDF extraction",
        )
        self.soft_assert_equal(
            fallback_result.strategy_attempts,
            ["pdf_ocr", "pdf_text"],
            "Default PDF extraction should try OCR before local text",
        )
        self.soft_assert_equal(
            fallback_result.selected_strategy,
            "pdf_text",
            "Unavailable OCR should fall through to local PDF text extraction",
        )

        # API rejections must reach System Activity, without raw provider payloads.
        for status_code, provider_message, error_code in (
            (400, "Invalid model: mistral-ocr-4-0", "1500"),
            (429, "Rate limit exceeded", "rate_limit"),
            (401, "Invalid API key: SECRET_OCR_KEY_SENTINEL", "unauthorized"),
            (502, "No structured provider error message was returned.", ""),
        ):
            rejected = Mock(status_code=status_code)
            rejected.json.return_value = {
                "message": provider_message,
                "type": "invalid_model" if status_code == 400 else "rate_limit",
                "code": error_code,
                "document": "PRIVATE_PROVIDER_DOCUMENT_SENTINEL",
            }
            rejected.text = json.dumps(rejected.json.return_value)
            if status_code == 502:
                rejected.text = "<html>PRIVATE_PROVIDER_DOCUMENT_SENTINEL</html>"
            with (
                patch("core.ingestion.service.secret_has_value", return_value=True),
                patch(
                    "core.ingestion.capabilities.secret_has_value", return_value=True
                ),
                patch(
                    "core.ingestion.strategies.mistral_ocr_common.get_secret_value",
                    return_value="SECRET_OCR_KEY_SENTINEL",
                ),
                patch(
                    "core.ingestion.strategies.mistral_ocr_common.requests.post",
                    return_value=rejected,
                ),
            ):
                failed_submission = service.submit(
                    sources="local-fallback.pdf", options={"strategies": ["pdf_ocr"]}
                )
                await get_runtime_context().ingestion_worker.run_once()
                failed_result = service.status(job_ids=failed_submission[0].job_id)[0]
            assert failed_result.status == "failed"
            activity = self.call_api("/api/system/activity-log?limit=200&tag=ingestion")
            assert activity.status_code == 200
            terminal = next(
                entry["data"]
                for entry in activity.json()["entries"]
                if entry.get("data", {}).get("event") == "ingestion_job_failed"
                and entry["data"].get("job_id") == failed_submission[0].job_id
            )
            assert (
                provider_message.replace("SECRET_OCR_KEY_SENTINEL", "[redacted]")
                in terminal["error"]
            )
            failure = terminal["extractor_failures"][0]
            assert failure["strategy"] == "pdf_ocr"
            assert failure["provider"] == "mistral"
            assert failure["http_status"] == status_code
            if error_code:
                assert failure["provider_error_code"] == error_code
            assert terminal["vault_name"] == vault.name
            assert "SECRET_OCR_KEY_SENTINEL" not in str(terminal)
            assert "PRIVATE_PROVIDER_DOCUMENT_SENTINEL" not in str(terminal)

        rejected.status_code = 400
        rejected.text = json.dumps({"message": "Invalid model: unavailable"})
        with (
            patch("core.ingestion.service.secret_has_value", return_value=True),
            patch("core.ingestion.capabilities.secret_has_value", return_value=True),
            patch(
                "core.ingestion.strategies.mistral_ocr_common.get_secret_value",
                return_value="test-key",
            ),
            patch(
                "core.ingestion.strategies.mistral_ocr_common.requests.post",
                return_value=rejected,
            ),
        ):
            fallback = service.submit(
                sources="local-fallback.pdf",
                options={"strategies": ["pdf_ocr", "pdf_text"]},
            )
            await get_runtime_context().ingestion_worker.run_once()
            fallback_status = service.status(job_ids=fallback[0].job_id)[0]
        assert fallback_status.status == "completed"
        assert fallback_status.selected_strategy == "pdf_text"
        activity = self.call_api("/api/system/activity-log?limit=200&tag=ingestion")
        completion = next(
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "ingestion_job_completed"
            and entry["data"].get("job_id") == fallback[0].job_id
        )
        assert (
            "Invalid model: unavailable" in completion["extractor_failures"][0]["error"]
        )

        await self.stop_system()
        self.teardown_scenario()
