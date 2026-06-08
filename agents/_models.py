"""Model selection for agents — re-exports the central registry.

The single source of truth is :mod:`shared.models`, so agents AND services
(edit_capture_handler, the eval judge in shared.rubrics) resolve models the
same way. Import the tier constants + resolvers from here or from
``shared.models`` directly:

    from agents._models import HEAVY, LIGHT, pick_model

Agents pass a TIER (``HEAVY`` / ``LIGHT``) — never a literal model name.
``make_llm_agent`` runs the tier through ``pick_model`` internally.
"""
from __future__ import annotations

from shared.models import (  # noqa: F401  (re-export)
    HEAVY,
    LIGHT,
    gen_content_config,
    heavy,
    light,
    model_for,
    pick_model,
)
