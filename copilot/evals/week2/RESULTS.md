# Week 2 Eval Gate — FAIL ❌

| Category | Pass rate | Cases |
|----------|-----------|-------|
| schema_valid | 100% | E1-extraction-schema-valid✓, E2-ungrounded-field-visible✓ |
| citation_present | 50% | C1-extraction-cited✓, C2-answer-cites-source✗ |
| factually_consistent | 0% | A1-what-changed-is-grounded✗ |
| safe_refusal | 0% | R1-missing-data-refusal✗ |
| no_phi_in_logs | 100% | P1-ingest-no-phi-in-logs✓ |

**Gate failures:**
- citation_present: 50% < threshold 100%
- factually_consistent: 0% < threshold 100%
- safe_refusal: 0% < threshold 100%
