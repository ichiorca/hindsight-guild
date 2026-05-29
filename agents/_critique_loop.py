"""LoopAgent wrapper for the Critique → Reviser pair.

Default pipeline runs Critique + Reviser exactly once. For high-severity
critiques, a single revision often isn't enough — the Reviser fixes the
obvious issues but the second pass would catch deeper problems. This
LoopAgent runs the pair up to ``max_iterations`` times, with an
escalation gate that short-circuits when the critique reports
severity="low" (no more work needed) or "medium" (acceptable).

Termination:
  - Critique reports severity="low" or "medium" → escalate (exit loop)
  - max_iterations reached → exit loop
  - Critique still reports severity="high" after max → loop exits anyway;
    Review Agent downstream will surface the persistent issue to the founder

The escalation gate is a custom BaseAgent (not an LlmAgent) because the
decision is purely deterministic on state['critique']['severity']. Using
an LLM here would burn tokens for a one-key lookup.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.loop_agent import LoopAgent
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions


class CritiqueEscalationGate(BaseAgent):
    """Deterministic gate. Reads state['<critique_state_key>']['severity'];
    emits escalate=True when severity is low/medium (loop should stop).
    """

    critique_state_key: str = "critique"
    accept_severities: tuple = ("low", "medium")

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        critique = ctx.session.state.get(self.critique_state_key) or {}
        # critique can land as a dict OR (less commonly) a JSON string —
        # be lenient.
        severity = None
        if isinstance(critique, dict):
            severity = critique.get("severity")
        elif isinstance(critique, str):
            # Quick lowercase keyword sniff. Avoids a json.loads roundtrip
            # for the common case where the critic returned bare JSON.
            blob = critique.lower()
            for s in ("low", "medium", "high"):
                if f'"severity": "{s}"' in blob or f'"severity":"{s}"' in blob:
                    severity = s
                    break

        should_escalate = severity in self.accept_severities
        # Always yield an event so the loop sees progress; only set
        # escalate=True when the gate decides to stop.
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(escalate=should_escalate),
        )


def build_critique_loop(
    *,
    name: str,
    critique_agent: BaseAgent,
    reviser_agent: BaseAgent,
    critique_state_key: str = "critique",
    max_iterations: int = 2,
    gate_name: str | None = None,
) -> LoopAgent:
    """Wrap a (critique, reviser) pair in a LoopAgent.

    Order in the loop: Critique → Reviser → EscalationGate. The gate
    decides AFTER the Reviser has produced its output whether another
    pass would help (severity still "high") or the work is done.

    Args:
        name: Name of the resulting LoopAgent (e.g., "drafting_loop").
        critique_agent: An LlmAgent that writes ``critique_state_key``.
        reviser_agent: An LlmAgent that reads ``critique_state_key`` and
            overwrites the source state key.
        critique_state_key: The state key the critique writes to.
        max_iterations: Hard cap. Default 2 (most drafts converge in 2).
        gate_name: Custom name for the escalation gate sub-agent.
    """
    gate = CritiqueEscalationGate(
        name=gate_name or f"{name}_gate",
        critique_state_key=critique_state_key,
    )
    return LoopAgent(
        name=name,
        description=(
            f"Loop: {critique_agent.name} → {reviser_agent.name} → gate. "
            f"Up to {max_iterations} iterations. Stops early when the "
            f"critique reports severity in {('low', 'medium')}."
        ),
        sub_agents=[critique_agent, reviser_agent, gate],
        max_iterations=max_iterations,
    )
