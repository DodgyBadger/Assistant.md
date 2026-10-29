"""Core applications use declared test credentials rather than ambient SDK keys."""

import os

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario

AMBIENT_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ASSISTANTMD_SECRETS_KEYS",
)


@pytest.fixture(autouse=True)
def ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AMBIENT_NAMES:
        monkeypatch.setenv(name, "synthetic-ambient-credential")


async def test_core_app_ignores_ambient_provider_and_keyring_credentials(
    app: Application,
) -> None:
    app.evidence.register_secret("synthetic-ambient-credential")
    assert all(name not in os.environ for name in AMBIENT_NAMES)
    vault = app.vault("CoreCredentialIsolationVault")
    await app.start()
    result = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "session_id": "explicit-test-model",
            "prompt": "Complete using the declared deterministic model.",
            "model": "test",
        },
    )
    assert result["task"]["status"] == "completed"
    assert result["terminal_event"]["event"] == "done"
