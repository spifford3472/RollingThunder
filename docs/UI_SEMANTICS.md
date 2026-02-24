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


### Topbar Core — Semantic Contract (Authoritative)

**Purpose**
The Topbar is always visible and read-only. It provides immediate situational awareness: identity/context, authoritative time, and high-level status signals. It must remain calm, predictable, and non-interactive.

**Layout**
The Topbar is split into three fixed regions:

1) **Left: Identity & Context**
- Shows the RollingThunder brand (graphic/logo) as the primary anchor.
- Shows the current page name below the brand in smaller text.
- No state-based color changes or alerts in this section.

2) **Middle: UTC Time Authority**
- Shows **UTC time** in 24-hour format as the primary element.
- Shows **UTC date** below in smaller text.
- The middle region is the primary “time readout” and should not be cluttered with status logic.

3) **Right: Status Cluster & Temperature**
The right region contains three icon indicators and temperature text.
Indicators must be **shape-first** (color is secondary).

**Indicators (shape-first, not color-first)**
A) **System Health**
- ✅ = healthy
- ❌ = unhealthy
- ⚠️ = degraded or stale
Derived from: `rt:system:health.ok` and `rt:system:health.stale`.

B) **Time Source**
- GPS time icon when time is derived from GPS
- Clock/system icon when time is derived from local system time
Derived from: presence of `rt:gps:time` (GPS) vs null/missing (system).

C) **GPS Fix**
- Fix-present icon when `rt:gps:fix === true`
- No-fix icon otherwise

**Temperature (text)**
Below the icons, show temperature in both units:
`<temp_f>°F / <temp_c>°C`
Derived from: `rt:environment:temp_f` and `rt:environment:temp_c` (or equivalent).

**Accessibility Rule (Non-Negotiable)**
User is red/green colorblind. Therefore:
- **Shape conveys meaning.**
- Color may reinforce meaning but must never be the only signal.
- Use distinct shapes (✅ / ❌ / ⚠️) as the primary cue.

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

## 8. GPS Semantics ##
The following semantics define how GPS-related state keys are interpreted
by the UI. These rules apply regardless of data source (system fallback,
gpsd, NMEA, or future integrations).

### rt:gps:fix
Represents the best-known GPS receiver fix status as observed by the GPS publisher.

Fields:
- `has_fix` (bool): true only when `fix_type >= 2`
- `fix_type` (int):
    - `0` = no fix / no receiver / unknown
    - `1` = time-only or “searching” (allowed but not a location fix)
    - `2` = 2D fix (lat/lon)
    - `3` = 3D fix (lat/lon/alt)
- `sats` (int): satellites used (0 if unknown)
- `source` (string): e.g. `gpsd`, `nmea`, `system`
` `last_update_ms` (int): publisher timestamp

Semantics:
- UI should treat `fix_type < 2` as “no usable location fix”.
- `has_fix` is derived truth: `has_fix = (fix_type >= 2)`.

rt:gps:time (hash)
Represents the best-known UTC time and its source.

Fields:
- `utc_iso` (string): ISO-8601 UTC timestamp, e.g. `2026-02-07T00:47:31Z`
- `source` (string):
     - `gps` = time derived from GPS receiver (preferred)
     - `system` = OS time (fallback)
- `last_update_ms' (int)

Semantics:
- UI may display UTC time even without a fix.
- If `source != gps`, time is “fallback” (not “bad”).Represents the best-known GPS receiver fix status as observed by the GPS publisher.

---

## 9. What the UI Must Never Do ##
The UI MUST NOT:
- Invent node state
- Guess status from timestamps
- Override controller decisions
- Collapse multiple fields into new meanings
- Introduce new status values

If something is unclear, the UI should display uncertainty, not confidence.


## 10. When This Document Must Be Updated ##
Update this document if:
- A new node status is introduced
- Severity rules change
- New required fields are added
- Badge semantics change
- UI rendering logic changes meaningfully

---

## 11. Navigation & Input Semantics (Authoritative) ##

This section defines the **authoritative input → intent → behavior** contract for the RollingThunder UI.
It exists to prevent “keyboard hacks” and to keep navigation deterministic across:
- physical controls (ESP32 buttons + rotary encoder)
- developer keyboard bindings (debug only)

The UI is **read-only**. It may highlight focus locally, but must not invent state transitions
or bypass controller rules.

### 11.1 Input Devices (Conceptual) ###

Physical controls (authoritative set):
- **Page Next / Page Prev**
- **Focus Next / Focus Prev**
- **Rotary Encoder Turn** (selection delta)
- **Rotary Press** (enter)
- **OK**
- **CANCEL**

Developer keyboard mappings are allowed for testing but MUST map to the same intent IDs.

---

### 11.2 Navigation State Machine ###

Navigation is a three-state machine:

1) **GLOBAL_FOCUS**
   - User is selecting *which panel* is active (highlighted).
   - No panel-internal selection changes occur.

2) **PANEL_BROWSE**
   - User is interacting *within the focused panel* (e.g., scrolling a list).
   - Rotary turn changes panel-local selection.
   - Panels may “preview-on-selection” (e.g., tune radio as selection changes) via controller-mediated intents.

3) **MODAL_DIALOG**
   - A modal confirmation/details dialog is open.
   - Input is captured: only OK/CANCEL are meaningful.

State transitions are deterministic:

| Current State  | Input / Intent | Next State | Notes |
|---|---|---|---|
| GLOBAL_FOCUS | `ui.ok` | PANEL_BROWSE or MODAL_DIALOG | Depends on panel capability (see 11.6) |
| GLOBAL_FOCUS | `ui.cancel` | GLOBAL_FOCUS (no-op) or Clear Focus | Page policy decides (see 11.5) |
| PANEL_BROWSE | `ui.cancel` | GLOBAL_FOCUS | Exit panel browse (rollback preview-only) |
| PANEL_BROWSE | `ui.ok` | MODAL_DIALOG | Enter modal confirm/details |
| MODAL_DIALOG | `ui.ok` | PANEL_BROWSE | Commit action, close modal |
| MODAL_DIALOG | `ui.cancel` | PANEL_BROWSE | Abort action, close modal |

---

### 11.3 Canonical UI Intent IDs ###

The UI emits only intent events. The controller is the authority that interprets them.

Required navigation intents:

- `ui.page.next`
- `ui.page.prev`
- `ui.focus.next`
- `ui.focus.prev`
- `ui.ok`
- `ui.cancel`

Optional “panel browse” intents (panel-specific, if supported):

- `ui.browse.delta` with params: `{ "delta": <int> }`
- `ui.browse.select` (equivalent to “enter/press” when in browse mode)

Notes:
- `ui.ok` and `ui.cancel` MUST be treated as semantic commits/aborts, not generic “buttons”.
- Panels MUST NOT invent new ad-hoc intent strings; all new intents must be added to `docs/INTENTS.md`.

---

### 11.4 Page Controls Allowlist (Non-Negotiable) ###

Each page declares:

```json
"controls": { "allowedIntents": [ ... ] }



If a future reader cannot answer:
`
“What does this color or badge mean?”
`
Then this document is incomplete.
---
End of UI Semantics Contract

---
**End of UI_SEMANTICS.md Document**
