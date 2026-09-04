"""Typed async SDK for the Hunar Voice Agents API.

Shared by both applications in this repository. Nothing in here knows
about hiring or sourcing; it only knows about Hunar.

The three pieces worth knowing about:

* :class:`~hunar_sdk.client.LiveHunarClient` and
  :class:`~hunar_sdk.fake.FakeHunarClient` both satisfy
  :class:`~hunar_sdk.protocol.HunarClient`, so demo mode is a type-safe
  swap rather than a branch scattered through the services.
* :func:`~hunar_sdk.webhooks.verify_webhook` authenticates inbound
  webhooks. The HMAC secret is the API key itself.
* :func:`~hunar_sdk.sanitize.sanitize` strips braces from operator text,
  because Hunar's prompt templating uses single-brace variables and a
  pasted job description will otherwise corrupt what the agent says.
"""

from __future__ import annotations

from .client import DEFAULT_BASE_URL, LiveHunarClient
from .enums import (
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    AgentStatus,
    CallStatus,
    EngagementStatus,
    Language,
    LifecycleStatus,
    SubmitState,
    VoicePersona,
    WebhookEventType,
)
from .errors import (
    HunarAuthError,
    HunarError,
    HunarNotFoundError,
    HunarQuotaError,
    HunarServerError,
    HunarTransportError,
    HunarValidationError,
    WebhookVerificationError,
)
from .models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    BulkCallCreate,
    BulkCallRecipient,
    BulkCallResult,
    Call,
    CallbackConfig,
    CallCreate,
    Guardrails,
    Page,
    PhoneNumber,
    RetryConfig,
    normalize_e164,
)
from .protocol import HunarClient
from .sanitize import (
    ALLOWED_PROMPT_VARIABLES,
    sanitize,
    sanitize_custom_data,
    strip_braces,
)
from .webhooks import (
    DEFAULT_MAX_SKEW_SECONDS,
    VerificationResult,
    compute_signature,
    verify_webhook,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_PROMPT_VARIABLES",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_SKEW_SECONDS",
    "NON_TERMINAL_STATUSES",
    "TERMINAL_STATUSES",
    "Agent",
    "AgentCreate",
    "AgentStatus",
    "AgentUpdate",
    "BulkCallCreate",
    "BulkCallRecipient",
    "BulkCallResult",
    "Call",
    "CallCreate",
    "CallStatus",
    "CallbackConfig",
    "EngagementStatus",
    "Guardrails",
    "HunarAuthError",
    "HunarClient",
    "HunarError",
    "HunarNotFoundError",
    "HunarQuotaError",
    "HunarServerError",
    "HunarTransportError",
    "HunarValidationError",
    "Language",
    "LifecycleStatus",
    "LiveHunarClient",
    "Page",
    "PhoneNumber",
    "RetryConfig",
    "SubmitState",
    "VerificationResult",
    "VoicePersona",
    "WebhookEventType",
    "WebhookVerificationError",
    "__version__",
    "compute_signature",
    "normalize_e164",
    "sanitize",
    "sanitize_custom_data",
    "strip_braces",
    "verify_webhook",
]
