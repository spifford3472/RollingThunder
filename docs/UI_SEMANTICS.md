# RollingThunder — UI Semantics (Authoritative) #

This document defines the **semantic contract** between the RollingThunder controller
(`rt-controller`) and all UI consumers (notably `rt-display`).

It exists to ensure:
- deterministic rendering
- stable visual meaning
- no UI-side guesswork
- no semantic drift over time

If controller behavior or UI rendering changes without updating this document,
the change is considered **incomplete**.

---

## 1. Scope and Philosophy ##

The RollingThunder UI is **read-only** and **purely representational**.

That means:
- The controller decides *truth*
- The UI decides *presentation*
- The UI never infers, guesses, or computes state transitions
- The UI must tolerate missing or legacy fields without crashing

The UI should be **boring, predictable, and honest**.

---

## 2. Canonical Node Status Model ##

The controller is the **single authority** for node state.

### 2.1 Canonical Status Values ###

All UI-facing APIs MUST emit exactly one of the following values
in the `status` field for each node:

| Status   | Meaning |
|----------|--------|
| `online`  | Node has reported presence within the configured freshness window |
| `stale`   | Node has not reported recently, but is not yet declared offline |
| `offline` | Node is considered unavailable |

No other values are permitted in UI payloads.

---

### 2.2 Legacy Normalization (Controller Responsibility) ###

If internal or legacy systems produce older status values, the controller MUST
normalize them before emitting UI payloads.

Canonical mappings:

| Legacy / Internal | UI Status |
|------------------|-----------|
| `up`, `alive`, `running`, `ok` | `online` |
| `down`, `dead`, `error` | `offline` |
| empty / unknown | `stale` |

**The UI must never receive `up`, `down`, or similar legacy values.**

---

## 3. Required Node Fields (UI Contract) ##

Each node object in `/api/v1/ui/nodes` MUST provide:

| Field | Type | Required | Notes |
|-----|------|----------|-------|
| `id` | string | yes | Stable node identifier |
| `role` | string | yes | `controller`, `display`, `radio`, etc |
| `status` | string | yes | One of `online|stale|offline` |
| `age_sec` | number or string | yes | Seconds since last presence |
| `last_seen_ms` | number | yes | Epoch milliseconds |
| `last_update_ms` | number | yes | Controller update timestamp |

Optional fields:

| Field | Type | Meaning |
|-----|------|---------|
| `hostname` | string | Informational |
| `ip` | string | Node IP address |
| `ui_render_ok` | boolean | Display-only health hint |
| `publisher_error` | string | Non-empty indicates ingest or publish issues |

---

## 4. UI Severity Model ##

The UI maps node state to **visual severity**, not meaning.

Severity values:

| Severity | Meaning |
|--------|--------|
| `ok` | Healthy |
| `warn` | Degraded or uncertain |
| `bad` | Unhealthy |

### 4.1 Base Severity Mapping ###

| Node Status | Severity |
|------------|----------|
| `online` | `ok` |
| `stale` | `warn` |
| `offline` | `bad` |

---

### 4.2 Severity Escalation Rules ###

Severity may only **escalate**, never downgrade.

The following conditions escalate severity to at least `warn`:

- `publisher_error` is non-empty
- `role === "display"` AND `ui_render_ok === false`

Once escalated, severity remains elevated even if base status is `online`.

---

## 5. Badges and Secondary Indicators ##

Badges are **additive hints**, not state transitions.

Allowed badges include:

| Badge | Condition |
|------|----------|
| `UI OK` | `role=display && ui_render_ok === true` |
| `UI degraded` | `role=display && ui_render_ok === false` |
| `publisher_error` | `publisher_error` non-empty |

Badges never override status; they only annotate it.

---

## 6. Rendering Rules (Non-Negotiable) ##

The UI renderer MUST follow these rules:

1. **Rendering is pure**
   - No mutation of input data
   - No time-based logic
   - No retries or inference

2. **Single classification point**
   - All logic flows through a single `classifyNode()` function
   - Rendering functions consume classification output only

3. **Stable ordering**
   - Nodes are sorted lexicographically by `id`

4. **Missing fields**
   - Missing optional fields are rendered as `-` or omitted
   - Rendering must never throw due to missing fields

---

## 7. Schema Versioning ##

All UI node payloads SHOULD include:

```json
"schema_version": "ui.nodes.v1"

This allows:
- forward compatibility
- rapid detection of contract drift
- safe future evolution

---

## 8. What the UI Must Never Do ##
The UI MUST NOT:
- Invent node state
- Guess status from timestamps
- Override controller decisions
- Collapse multiple fields into new meanings
- Introduce new status values

If something is unclear, the UI should display uncertainty, not confidence.

## 9. When This Document Must Be Updated ##
Update this document if:
- A new node status is introduced
- Severity rules change
- New required fields are added
- Badge semantics change
- UI rendering logic changes meaningfully

If a future reader cannot answer:
`
“What does this color or badge mean?”
`
Then this document is incomplete.
---
End of UI Semantics Contract

---
**End of UI_SEMANTICS.md Document**
