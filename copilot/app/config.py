"""Environment configuration — STUB (no implementation yet).

Fail closed at startup: required settings missing -> error, never a
half-configured service.
"""
from __future__ import annotations

import os
from typing import Mapping

from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_RULES_PATH = "rules/clinical_rules.json"


class Settings(BaseModel):
    openemr_fhir_base_url: str      # OPENEMR_FHIR_BASE_URL (required)
    anthropic_api_key: str          # ANTHROPIC_API_KEY (required)
    anthropic_model: str            # ANTHROPIC_MODEL (default DEFAULT_MODEL)
    clinical_rules_path: str        # CLINICAL_RULES_PATH (default DEFAULT_RULES_PATH)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (defaults to ``os.environ``)."""
    source = os.environ if env is None else env

    missing = [key for key in ("OPENEMR_FHIR_BASE_URL", "ANTHROPIC_API_KEY") if not source.get(key)]
    if missing:
        raise ValueError(f"missing required settings: {', '.join(missing)}")

    return Settings(
        openemr_fhir_base_url=source["OPENEMR_FHIR_BASE_URL"],
        anthropic_api_key=source["ANTHROPIC_API_KEY"],
        anthropic_model=source.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        clinical_rules_path=source.get("CLINICAL_RULES_PATH", DEFAULT_RULES_PATH),
    )
