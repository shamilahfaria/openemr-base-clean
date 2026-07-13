"""Environment configuration.

Fail closed at startup: required settings missing -> error, never a
half-configured service.

One OpenEMR base is enough. Set ``OPENEMR_BASE_URL`` and the standard FHIR mount
(``/apis/default/fhir``) is derived; readiness, the chart tools, and the demo
token all resolve from it via ``resolve_openemr_urls``. ``OPENEMR_FHIR_BASE_URL``
remains an override for a non-standard mount, and setting only it still works
(the base is recovered).
"""
from __future__ import annotations

import os
from typing import Mapping

from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_RULES_PATH = "rules/clinical_rules.json"
FHIR_MOUNT = "apis/default/fhir"


class Settings(BaseModel):
    openemr_base_url: str           # OPENEMR_BASE_URL (or recovered from the FHIR url)
    openemr_fhir_base_url: str      # OPENEMR_FHIR_BASE_URL (or derived: base + /apis/default/fhir)
    anthropic_api_key: str          # ANTHROPIC_API_KEY (required)
    anthropic_model: str            # ANTHROPIC_MODEL (default DEFAULT_MODEL)
    clinical_rules_path: str        # CLINICAL_RULES_PATH (default DEFAULT_RULES_PATH)


def resolve_openemr_urls(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Resolve (base_url, fhir_url) from whichever of ``OPENEMR_BASE_URL`` /
    ``OPENEMR_FHIR_BASE_URL`` is set, so callers need only one.

    Returns ``(None, None)`` when neither is set (the caller decides whether
    that is fatal). An explicit FHIR url overrides the derived mount; if only the
    FHIR url is set, the base is recovered by stripping the mount.
    """
    base = (env.get("OPENEMR_BASE_URL") or "").strip().rstrip("/")
    fhir = (env.get("OPENEMR_FHIR_BASE_URL") or "").strip().rstrip("/")
    if not base and not fhir:
        return None, None
    if not fhir:
        fhir = f"{base}/{FHIR_MOUNT}"
    if not base:
        base = fhir
        for suffix in (f"/{FHIR_MOUNT}", "/fhir"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    return base, fhir


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (defaults to ``os.environ``)."""
    source = os.environ if env is None else env

    base_url, fhir_url = resolve_openemr_urls(source)
    missing = []
    if not fhir_url:
        missing.append("OPENEMR_BASE_URL")
    if not source.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise ValueError(f"missing required settings: {', '.join(missing)}")

    return Settings(
        openemr_base_url=base_url,
        openemr_fhir_base_url=fhir_url,
        anthropic_api_key=source["ANTHROPIC_API_KEY"],
        anthropic_model=source.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        clinical_rules_path=source.get("CLINICAL_RULES_PATH", DEFAULT_RULES_PATH),
    )
