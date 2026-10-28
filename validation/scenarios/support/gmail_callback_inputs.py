"""Genuine JSON configuration parsing inside public Gmail constructor callbacks."""

from __future__ import annotations

import json

from validation.scenarios.support.gmail_http import http_factory


class ConfiguredGmailCallbacks:
    def __init__(self, runtime, authority, connection_id, provider, *, configured_json):
        self.runtime = runtime
        self.authority = authority
        self.connection_id = connection_id
        self.provider = provider
        self.configured_json = configured_json
        self.original_error = None
        self.original_message = None
        self.parse_count = 0

    def parse_configuration(self):
        self.parse_count += 1
        try:
            return json.loads(self.configured_json)
        except ValueError as error:
            self.original_error = error
            self.original_message = str(error)
            raise

    async def access_token(self):
        self.parse_configuration()
        return await self.runtime.google_oauth.access_token(
            self.authority, self.connection_id
        )

    def client_factory(self, authority):
        from core.integrations.google.gmail import GmailAPIClient

        self.parse_configuration()
        return GmailAPIClient(
            access_token_provider=lambda: self.runtime.google_oauth.access_token(
                authority, self.connection_id
            ),
            http_client_factory=http_factory(self.provider),
        )
