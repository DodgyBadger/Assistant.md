import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI

from core.runtime.paths import (
    resolve_bootstrap_data_root,
    resolve_bootstrap_system_root,
    set_bootstrap_roots,
)

# Prime bootstrap roots before importing modules that touch settings/paths
_BOOTSTRAP_DATA_ROOT = resolve_bootstrap_data_root()
_BOOTSTRAP_SYSTEM_ROOT = resolve_bootstrap_system_root()
set_bootstrap_roots(_BOOTSTRAP_DATA_ROOT, _BOOTSTRAP_SYSTEM_ROOT)

from api.application import create_application  # noqa: E402
from api.services import set_system_startup_time  # noqa: E402
from core.advanced_shell import load_advanced_shell_config  # noqa: E402
from core.advanced_shell.capability import AdvancedShellCapabilityService  # noqa: E402
from core.advanced_shell.preflight import AdvancedShellPreflightService  # noqa: E402
from core.authentication import load_authentication_policy  # noqa: E402
from core.authentication.models import AuthenticationMode  # noqa: E402
from core.logger import UnifiedLogger  # noqa: E402
from core.runtime.bootstrap import bootstrap_runtime  # noqa: E402
from core.runtime.config import RuntimeConfig  # noqa: E402
from core.settings import get_app_settings  # noqa: E402
from core.tools.advanced_shell import ShellTransportConfig  # noqa: E402

# Create main logger
logger = UnifiedLogger(tag="main")
app_settings = get_app_settings()
advanced_shell_config = load_advanced_shell_config(app_settings)
authentication_policy = load_authentication_policy(app_settings)
_shell_key_root_value = os.environ.get("ASSISTANTMD_SHELL_KEY_ROOT", "").strip()
_shell_key_root = Path(_shell_key_root_value) if _shell_key_root_value else None
advanced_shell_preflight = AdvancedShellPreflightService(
    advanced_shell_config, _BOOTSTRAP_SYSTEM_ROOT, key_root=_shell_key_root
)
advanced_shell_capability = AdvancedShellCapabilityService(
    advanced_shell_config,
    ShellTransportConfig.from_infrastructure(
        advanced_shell_config, _BOOTSTRAP_SYSTEM_ROOT, key_root=_shell_key_root
    ),
    advanced_shell_preflight,
)


# Run in development
# uvicorn main:app --host 0.0.0.0 --port 8000 --reload


#######################################################################
## FastAPI lifespan with runtime bootstrap
#######################################################################


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    startup_time = datetime.now()
    set_system_startup_time(startup_time)

    # Create runtime configuration for production
    config = RuntimeConfig.for_production(
        data_root=_BOOTSTRAP_DATA_ROOT,
        system_root=_BOOTSTRAP_SYSTEM_ROOT,
        public_url=app_settings.public_url,
    )

    # Bootstrap runtime services
    runtime = await bootstrap_runtime(config, advanced_shell=advanced_shell_capability)

    # Store runtime context in app state for API access
    app.state.runtime = runtime

    if (
        advanced_shell_config.enabled
        and authentication_policy.mode is AuthenticationMode.DISABLED
    ):
        logger.warning(
            "Advanced shell can reach the unauthenticated AssistantMD API",
            data={
                "event": "advanced_shell_security_posture_warning",
                "execution_mode": advanced_shell_config.execution_mode.value,
                "auth_mode": authentication_policy.mode.value,
                "reason": "advanced_shell_peer_has_unauthenticated_api_access",
            },
        )

    execution_mode = advanced_shell_config.execution_mode.value
    logger.info(
        f"Application startup complete in {execution_mode} execution mode",
        data={
            "event": "application_startup_completed",
            "status": "ready",
            "execution_mode": execution_mode,
            "advanced_shell_enabled": advanced_shell_config.enabled,
        },
    )

    try:
        yield  # App runs here
    finally:
        # Shutdown
        if hasattr(app.state, "runtime") and app.state.runtime:
            await app.state.runtime.shutdown()
            app.state.runtime = None  # Clear app state to match global context
            logger.info("Application shutdown complete")


#######################################################################
## FastAPI application setup
#######################################################################

app = create_application(
    authentication_policy=authentication_policy,
    advanced_shell_config=advanced_shell_config,
    advanced_shell_preflight=advanced_shell_preflight,
    lifespan=lifespan,
)


# Set up unified logging with instrumentation
logger.setup_instrumentation(app)
