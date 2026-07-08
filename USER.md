# USER.md — Target User & Use Cases

> Source of truth for the AgentForge Clinical Co-Pilot. Every agent capability
> in `ARCHITECTURE.md` must trace back to a use case defined here. If a
> capability does not serve a use case below, it does not belong in v1.

## The User (One Narrow Persona)

**Maria — Inpatient Hospice / Palliative Care RN.**

- **Setting:** A hospital-based hospice / palliative care unit (general inpatient,
  "GIP" level of care), where patients are admitted for symptom crises and
  end-of-life care.
- **Shift:** 12-hour day shift (07:00–19:30), typically **4–5 assigned patients**
  whose conditions can change hour to hour.
- **Ratio reality:** She is never caring for one patient at a time. She cycles
  between rooms managing symptom control (pain, dyspnea, agitation, nausea,
  secretions), titrating comfort medications against standing orders, and
  supporting families — all while charting.
- **Tech context:** OpenEMR is already open on the workstation-on-wheels (WOW)
  she pushes room to room. She lives *inside* the patient chart, not in a
  separate analytics tool.
- **What she is NOT:** She does not diagnose, prescribe, or place orders. Her job
  is to **read, assess, act on existing comfort-care orders, and document
  accurately** — which is exactly why a *read-only* assistant fits her role and
  keeps the trust boundary clean.

### Why this user (and not "physicians need help")

An inpatient hospice RN is a high-frequency chart-reader working under acute
time pressure with an unusually low tolerance for error. The stakes are
distinctive to this domain: comfort-medication regimens (opioids,
benzodiazepines, antiemetics, anticholinergics) carry real dosing risk, and
**goals-of-care and code status** are chart facts that must never be
misreported. She repeatedly performs the same synthesis — "what changed, what's
the current symptom burden, what am I about to give, and what are this patient's
wishes." That repetition, under pressure, across several rapidly-changing
patients, is precisely the shape of problem an agent is good at. Picking her
narrows *everything* downstream: the data the agent needs (comfort meds and PRN
usage, allergies, latest labs/vitals, active problems, recent notes/encounters,
and goals-of-care / advance-directive context), the latency budget (seconds, at
a WOW, mid-workflow), and what the agent must **refuse** to do (anything write,
diagnostic, or outside this patient's record).

### Her tolerances and hard limits

| Dimension | Maria's tolerance |
|-----------|-------------------|
| Latency | Answer must land in **seconds** while she stands at the WOW. A minute is a failure. |
| Wrong answer | **Unacceptable.** A confidently wrong comfort-med dose, allergy, or **code-status** claim can cause direct harm or violate a patient's end-of-life wishes. One bad answer ends her trust. |
| Uncertainty | She *prefers* "I can't verify that — here's the recent visit history" over a smooth guess. |
| Scope | She wants *this patient, right now* — not general medical Q&A, not other patients. |
| Verifiability | Every clinical claim must be traceable to the chart so she can click through and confirm before she acts on it. |

## Her Workflow — The Moment the Agent Enters Maria's Day

The Co-Pilot is a **sidebar inside the OpenEMR patient chart**. It appears at the
exact moments Maria is already reading the chart, so it never forces a context
switch out of her workflow.

**The anchor scenario — start of shift, 07:00–07:30.**
Maria takes handoff on 5 patients she may not have had yesterday. For each, in
the ~30 seconds before she walks into the room, she needs to answer: *Who is
this, what is their current symptom burden, what changed since the last shift,
and what are their goals of care / code status?* Today that means opening the
chart and scanning notes, the MAR/PRN history, labs, and vitals across several
tabs, per patient, while report is still going. This is where the agent earns
its place.

**The recurring scenario — before every PRN comfort med, family conversation, or
documentation event.**
Throughout the shift, before Maria gives a PRN comfort medication, speaks with a
family, or charts an assessment, she re-opens the chart to confirm the relevant
context — and her questions naturally **build on each other**: *"What's her pain
regimen?"* → *"When was the last PRN morphine given?"* → *"Any allergy to the
alternatives?"* She asks, reads a concise sourced answer, follows up, acts, and
charts.

**The output she does something with:** a short, scannable, **source-cited**
answer she can trust enough to act on — or an explicit, safe fallback when the
agent cannot verify a claim.

## Interaction Model — Multi-Turn (Session-Scoped)

v1 supports **multi-turn conversation scoped to a single patient chart session.**
The agent maintains context across Maria's follow-up questions so she does not
have to restate the patient or repeat prior context on every turn. This is not
open-ended chat: the conversation is bounded to the one patient whose chart is
open, and it does **not** carry memory across different patients or across
shifts. Each new chart session starts a fresh conversation. This directly serves
UC1 and UC2, where the nurse's real questions arrive as a chain of dependent
follow-ups, not as isolated one-shot queries.

## Use Cases

Each use case states the nurse moment, what the agent returns, and — as the
assignment requires — an explicit answer to **why a conversational (multi-turn)
agent is the right shape here** (versus a dashboard, a sorted list, or a better
chart view).

### UC1 — "What changed / current comfort status" patient context

- **Moment:** Maria opens a chart at start of shift (or before re-entering a
  room) and asks: *"What's the key context on this patient right now?"* — then
  drills in: *"Tell me more about the pain control overnight."*
- **Agent returns:** Recent visit/encounter context, current symptom burden and
  notable changes since the last shift, goals-of-care / code status where
  documented, and the patient-specific facts that matter today — each claim cited
  to its source record.
- **Why a multi-turn agent (not a dashboard):** The answer is a **synthesis
  across notes, comfort meds, labs, vitals, and encounters** that a static
  dashboard cannot prioritize for *this* patient at *this* moment, and Maria's
  need is inherently **iterative** — the first answer surfaces what changed, and
  she immediately drills into the symptom that matters most. Multi-turn lets her
  refine without restating context; a dashboard shows everything with equal
  weight and cannot follow a train of thought.

### UC2 — Targeted review before a PRN comfort med or documentation

- **Moment:** Before giving a PRN comfort medication or charting, Maria asks a
  focused question and follows the thread — e.g. *"Any allergy or interaction
  concern before I give this med?"* → *"When was it last given and how much?"* →
  *"What's the latest respiratory rate?"*
- **Agent returns:** The specific clinically-relevant slice (comfort medications
  and PRN/last-dose history, allergies, labs, vitals, active problems) with
  source IDs, plus a clear label on anything that falls **outside the patient's
  record**.
- **Why a multi-turn agent (not a search bar or chart view):** Maria's goal is
  **interpretation, not raw retrieval**, and the safety check is a *chain* —
  allergy, then last dose, then a vital sign — where each question depends on the
  last answer. Cross-referencing an allergy list against a med list against PRN
  timing across four tabs under time pressure is exactly where errors happen. The
  agent carries that context across turns and can say "nothing relevant found"
  instead of making her prove a negative by clicking through empty views.

### UC3 — Safe refusal & fallback (the trust-boundary use case)

- **Moment:** A tool fails, the record is incomplete, a claim can't be verified,
  or the request is out of scope / attempts to reach data Maria isn't authorized
  to see (e.g. another patient, or general medical advice).
- **Agent returns:** Either (a) a clearly-labeled **fallback** to the most recent
  verified visit history, or (b) an explicit refusal — never an unsupported
  clinical claim dressed up as fact.
- **Why an agent (and why this is a real use case, not just error handling):** In
  end-of-life care, *how the system behaves when it doesn't know* is a
  first-class feature. Maria will only adopt a tool she can trust to **fail
  loudly and safely**. A conversational agent can degrade gracefully within the
  conversation — narrowing scope and telling her it did — where a dashboard
  either shows stale/blank cells silently or crashes. This use case exists so
  that trust survives the unhappy path, which is where clinical tools actually
  get abandoned.

## Traceability to the Agent Build

| Capability in `ARCHITECTURE.md` | Justifying use case |
|--------------------------------|---------------------|
| Patient-summary + recent-encounters tools | UC1 |
| Comfort-meds / allergies / labs / vitals / problems / notes retrieval tools | UC2 |
| Goals-of-care / code-status surfacing | UC1 |
| Source-attribution verification layer | UC1, UC2 (invariant: claims cite a source) |
| Recent-visit-history fallback + refusal path | UC3 |
| Read-only, patient-scoped access enforced via OpenEMR session | UC2, UC3 (authorization boundary) |
| Multi-turn conversation, scoped to one patient chart session | UC1, UC2 — the nurse's real questions arrive as chains of dependent follow-ups |

**What is deliberately *not* here (and therefore not built in v1):** conversation
memory across different patients or across shifts, write/order/prescribe actions,
general medical Q&A, and cross-patient queries. None of them trace to a use case
above, so per the assignment's own rule they stay out of v1.

---

### Notes / open decisions

- **Persona:** anchored on an **inpatient hospice / palliative care RN**. The
  anchor scenario is the start-of-shift "what changed overnight + goals of care"
  moment; comfort-med safety and code-status accuracy are the sharpest stakes.
- **Interaction model:** v1 is **multi-turn, scoped to a single patient chart
  session** — chosen because UC1 and UC2 are genuinely iterative. Memory does not
  persist across patients or shifts; that boundary keeps each conversation
  verifiable and is the line to defend in the interview.
- **Goals-of-care / code status:** this data may be inconsistently structured in
  the OpenEMR sample data — confirm during the audit which fields hold it, since
  a wrong code-status claim is the highest-severity failure for this user.
