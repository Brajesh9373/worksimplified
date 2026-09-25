"""Trace engine — thin wrapper over shared.traceability (no new ID scheme)."""

from __future__ import annotations

from typing import Any


def coverage(state: dict[str, Any]) -> dict[str, Any]:
    from shared.traceability import coverage_report

    return coverage_report(state)


def chain(state: dict[str, Any], seed_id: str) -> dict[str, Any]:
    from shared.traceability import chain_for

    return chain_for(state, seed_id)
