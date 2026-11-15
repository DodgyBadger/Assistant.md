"""Real telemetry export sink, without retaining private payloads as evidence."""

from __future__ import annotations

import logging
from contextlib import contextmanager

import logfire
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from core.logger import UnifiedLogger, refresh_logfire_configuration
from validation.support.runtime.authentication import authentication_application


@contextmanager
def private_telemetry(app):
    exporter = TestExporter()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        metrics=False,
        scrubbing=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
    )
    application = authentication_application(app, "disabled")
    UnifiedLogger(tag="browser-privacy-instrumentation").setup_instrumentation(
        application
    )
    # pytest already owns root logging handlers, so basicConfig cannot add the
    # production handler. Attach that same real handler to this external sink.
    handler = logfire.LogfireLoggingHandler()
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield application, exporter
    finally:
        logfire.force_flush()
        root.removeHandler(handler)
        root.setLevel(original_level)
        handler.close()
        refresh_logfire_configuration(force=True)
