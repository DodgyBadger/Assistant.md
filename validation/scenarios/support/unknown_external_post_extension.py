"""Configured external operation with an explicitly unknown recovery policy."""

from core.tools.base import ToolRecoveryPolicy
from validation.scenarios.support import external_post_extension


class UnknownExternalPostWorkload(external_post_extension.ExternalPostWorkload):
    TOOL_NAME = "unknown_external_post_workload"
    POLICY = ToolRecoveryPolicy.UNKNOWN
