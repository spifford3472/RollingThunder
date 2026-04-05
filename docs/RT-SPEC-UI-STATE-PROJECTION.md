# RT-SPEC-UI-STATE-PROJECTION.md
## RollingThunder v0.50.5 — UI State Projection
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative controller-owned **UI state projection layer** for RollingThunder.

It specifies how the controller projects interaction truth into Redis so that non-authoritative consumers can render, react, and derive feedback without owning semantics.

This specification exists because the system already has:

- intent vocabulary
- input normalization
- interaction state machine rules
- intent execution rules
- execution engine rules
- hardware LED rendering transport

But it does **not** yet have a locked controller-owned Redis projection for UI interaction state.

This document fills that gap.

It defines:

- what UI state the controller must project into Redis
- when projection updates occur
- which parts of state are authoritative versus derived
- how projection interacts with page transitions, modals, browse, focus, results, and degraded conditions
- how renderer-only consumers such as `rt-display` and the console LED sender must consume that state

This document does **not** define:

- browser rendering details
- panel firmware behavior
- serial LED transport format
- panel raw input format
- downstream service business logic
- intent vocabulary additions

---

# 2. Core Principles

## 2.1 Controller Owns UI Meaning

The controller is the sole authority for UI meaning.

That includes:

- current page
- focused panel
- effective interaction layer
- browse activation
- modal activation
- last accepted/rejected result posture
- any operator-facing degraded trust posture

No browser runtime, panel firmware, or console LED sink may infer these independently.

## 2.2 Redis Is the Authoritative Projection Surface

The controller must project UI state into Redis.

Redis is the authoritative shared surface for:

- renderer-only UI consumers
- controller-driven LED derivation
- diagnostic inspection
- future automation or secondary read-only integrations

In-memory runtime state may still exist inside the controller, but Redis is the required projection boundary.

## 2.3 Projection Is State, Not Rendering

Projected UI state must express **what is true**, not how it should look.

Good examples:

- current page = `pota`
- focused panel = `pota_spots_summary`
- modal active = true
- browse active = true
- last result = rejected_busy

Bad examples:

- header color blue
- button glows brighter
- modal uses large font

## 2.4 Projection Happens After Authoritative Decisions

UI state projection must only update after authoritative controller decisions.

That means:

- after validated page transition commit
- after validated focus mutation
- after validated browse mutation
- after modal creation or dismissal
- after execution result classification
- after degraded trust decisions

It must not update optimistically on raw input.

## 2.5 Same Truth, Same Projection

The same authoritative controller state must always produce the same projected UI state.

Projection must be deterministic.

---

# 3. Why This Layer Exists

RollingThunder already requires:

- Redis as source of truth for shared state
- controller-owned semantics
- renderer-only UI
- LEDs derived from controller state rather than button presses
- serialized authoritative execution

The LED state model explicitly expects controller-owned UI truth such as current page, focused panel, browse state, modal state, and recent result state to exist in Redis-backed controller state. The serial LED contract also expects the controller to derive canonical LED meaning from controller truth and resync the console with `reset_leds` followed by a full `snapshot`. fileciteturn0file2L133-L147 fileciteturn0file2L330-L339 fileciteturn0file0L287-L295

This projection layer is the missing bridge between controller execution and renderer-only consumers.

---

# 4. Projection Scope

The controller must project at least these categories of state:

1. page state
2. focus state
3. interaction-layer state
4. browse state
5. modal state
6. recent result state
7. degraded / authority state
8. optional page-specific contextual state references

These are controller-owned projections.

Consumers may read them.
Consumers may not author them.

---

# 5. Canonical Redis Keys

## 5.1 Required Base Keys

The following keys are required.

### `rt:ui:page`

Type:
- string

Meaning:
- current authoritative page id

Example:

```text
rt:ui:page = pota
```

### `rt:ui:focus`

Type:
- string

Meaning:
- current authoritative focused panel id
- empty or absent only if no focused panel is valid for the current page

Example:

```text
rt:ui:focus = pota_spots_summary
```

### `rt:ui:layer`

Type:
- string

Allowed values:
- `default`
- `browse`
- `modal`
- `transient`
- `degraded`

Meaning:
- current effective top interaction layer after controller priority resolution

### `rt:ui:modal`

Type:
- string JSON object

Meaning:
- current authoritative modal projection
- absent or deleted when no modal is active

### `rt:ui:browse`

Type:
- string JSON object

Meaning:
- current browse projection for the effective focused panel and page
- absent or deleted when browse is not active

### `rt:ui:last_result`

Type:
- string JSON object

Meaning:
- recent controller-owned execution/result projection
- optional TTL recommended

### `rt:ui:authority`

Type:
- string JSON object

Meaning:
- controller trust / degraded posture relevant to UI and LEDs

---

## 5.2 Optional Indexed Browse Key

In addition to `rt:ui:browse`, the controller may publish:

### `rt:ui:browse:<panel_id>`

Type:
- string JSON object

Meaning:
- panel-specific browse projection keyed by focused or browsable panel id

Rule:
- If this indexed form exists, `rt:ui:browse` must still reflect the current effective browse context so simple consumers do not need panel-specific logic.

---

## 5.3 Optional Page Context Key

### `rt:ui:page_context`

Type:
- string JSON object

Meaning:
- page-level derived context reference useful to renderers

Examples:
- selected POTA band
- selected POTA park ref
- currently highlighted row identifier

This key is optional and page-family specific.
It must not replace required core keys.

---

# 6. Canonical Value Schemas

## 6.1 `rt:ui:modal`

Canonical shape:

```json
{
  "id": "<stable modal instance id>",
  "type": "<modal type>",
  "title": "<optional title>",
  "confirmable": true,
  "cancelable": true,
  "destructive": false,
  "context": {
    "page": "<page id>",
    "focused_panel": "<panel id|null>"
  },
  "opened_at_ms": 0
}
```

Rules:

- `id` must uniquely identify the modal instance
- `confirmable` and `cancelable` are controller-derived truth
- `destructive` must be true only when the modal represents destructive or safety-critical confirmation posture
- renderer may display this state but must not derive additional authority from it

## 6.2 `rt:ui:browse`

Canonical shape:

```json
{
  "active": true,
  "page": "<page id>",
  "panel": "<panel id>",
  "selected_index": 0,
  "selected_id": "<optional stable item id>",
  "count": 0,
  "updated_at_ms": 0
}
```

Rules:

- `active` must be true if the key exists
- `page` and `panel` must match the authoritative interaction context
- `selected_index` alone is acceptable for a first implementation if stable item ids are not yet available
- `selected_id` is preferred when a stable item identity exists

## 6.3 `rt:ui:last_result`

Canonical shape:

```json
{
  "result": "accepted_completed|accepted_pending|accepted_noop|rejected_post_validation|execution_failed|execution_timeout",
  "intent": "<intent>",
  "reason": "<stable reason code|null>",
  "execution_id": "<id|null>",
  "page": "<page id|null>",
  "focused_panel": "<panel id|null>",
  "ts_ms": 0
}
```

Rules:

- This projection is derived from controller execution truth
- It must never claim success that the controller has not yet determined
- TTL is recommended so the state decays naturally

## 6.4 `rt:ui:authority`

Canonical shape:

```json
{
  "controller_authoritative": true,
  "degraded": false,
  "stale": false,
  "reason": "<stable reason code|null>",
  "ts_ms": 0
}
```

Rules:

- `controller_authoritative` indicates whether normal UI meaning remains trustworthy
- `degraded` and `stale` are controller-owned decisions
- consumers must fail closed when this projection indicates degraded or stale trust

---

# 7. Projection Update Rules

## 7.1 Page Projection

The controller must update `rt:ui:page` only after page transition cutover commits.

It must not update on raw `ui.page.next` request alone.

This preserves the page transition rule that the new page becomes authoritative only after the validated transition completes. fileciteturn0file9L579-L597

## 7.2 Focus Projection

The controller must update `rt:ui:focus` only after focus mutation commits.

If the new page has a default focus, that value must be projected during page cutover.

## 7.3 Interaction Layer Projection

The controller must project the effective interaction layer into `rt:ui:layer` after interaction state resolution.

Priority must follow the interaction and LED models:

1. modal
2. destructive / critical safety
3. degraded authority
4. browse
5. default page context

This aligns with the LED priority model and interaction state rules. fileciteturn0file2L503-L521 fileciteturn0file9L357-L369

## 7.4 Browse Projection

When browse becomes active:

- controller must write `rt:ui:browse`
- controller must update `rt:ui:layer` to `browse` unless a higher-priority layer applies

When browse ends:

- controller must delete `rt:ui:browse`
- controller must recompute `rt:ui:layer`

## 7.5 Modal Projection

When a modal opens:

- controller must write `rt:ui:modal`
- controller must update `rt:ui:layer` to `modal`

When a modal closes:

- controller must delete `rt:ui:modal`
- controller must recompute `rt:ui:layer`

## 7.6 Result Projection

When controller execution produces a terminal or pending result relevant to operator awareness:

- controller may update `rt:ui:last_result`
- recommended TTL: 2 to 10 seconds depending on semantics

Result projection must occur only after the controller has authoritatively classified the result. This follows the execution engine and LED model requirement that feedback reflects post-controller truth rather than optimistic input. fileciteturn0file2L24-L41 fileciteturn0file9L180-L188

## 7.7 Authority / Degraded Projection

When controller trust degrades or staleness is detected:

- controller must update `rt:ui:authority`
- controller must recompute `rt:ui:layer`
- controller must not continue projecting ordinary optimistic UI state without marking degraded truth

This is required by the LED failure model. fileciteturn0file2L524-L567

---

# 8. Required Deletion Semantics

Projection keys that no longer apply must be deleted, not left stale.

Required examples:

- when no modal exists, delete `rt:ui:modal`
- when browse is inactive, delete `rt:ui:browse`
- when page-specific context no longer applies, delete or fully replace `rt:ui:page_context`

It is better to remove a projection than to leave stale meaning in Redis.

---

# 9. Projection Writer Ownership

## 9.1 Single Writer Rule

Only one controller-owned component may author the `rt:ui:*` projection family.

Recommended owner:
- `ui_state_projector.py`

No other service may independently mutate these keys in routine operation.

## 9.2 Inputs to the Projector

The projector may read from:

- controller execution result stream
- controller-owned page transition state
- controller-owned interaction state
- controller-owned modal state
- controller-owned page context state
- controller-owned degraded/authority state

The projector must not read raw panel input and infer state from it directly.

---

# 10. Recommended Runtime Architecture

## 10.1 Projector Role

`ui_state_projector.py` should be a controller-side service whose only responsibility is to translate authoritative runtime state into stable Redis UI projection keys.

It is not a renderer.
It is not an executor.
It is not a serial driver.

## 10.2 Recommended Inputs for Initial Implementation

A first implementation may use any controller-owned state source already available, including:

- controller runtime snapshot state
- execution results
- page transition events
- interaction state reducer outputs

If no consolidated state source exists yet, the projector may temporarily consume a narrow controller-owned internal projection file or bus event stream, provided it remains the single writer of `rt:ui:*` keys.

## 10.3 Publication Strategy

Recommended pattern:

- compute full effective projection each cycle or on each authoritative state change
- compare with last projected value
- write only changed keys
- delete keys no longer applicable

This keeps Redis stable and avoids unnecessary churn.

---

# 11. Integration with LED Sender

The console LED sender must consume this projection family rather than inventing UI state names ad hoc.

At minimum, it should read:

- `rt:ui:page`
- `rt:ui:focus`
- `rt:ui:modal`
- `rt:ui:browse`
- `rt:ui:last_result`
- `rt:ui:authority`

This ensures LED meaning remains controller-owned and derived from the same authoritative UI truth the renderer sees. That is required by both the LED state model and the serial LED contract. fileciteturn0file2L72-L90 fileciteturn0file0L15-L22

---

# 12. Integration with UI Runtime

The browser runtime remains renderer-only.

It may read `rt:ui:*` state directly or via controller snapshot APIs.

It must not become a second writer of UI semantic state.

If a snapshot API is used, that API must expose controller-owned projection state, not browser-derived interpretation.

---

# 13. Failure Handling

## 13.1 Missing Upstream State

If required upstream controller state is unavailable:

- the projector must fail closed
- it must not invent page, focus, or modal truth
- it may retain only clearly valid degraded authority state

## 13.2 Redis Failure

If Redis is unavailable:

- projector must not assume projection succeeded
- it must retry safely
- no consumer may infer updated UI truth without Redis confirmation

## 13.3 Stale Projection Prevention

If the projector cannot prove a projection is still valid:

- it must delete or replace the stale key
- not leave old meaning behind

---

# 14. First Implementation Scope

The first implementation of `ui_state_projector.py` should be intentionally narrow.

## 14.1 Required in First Version

Must project:

- `rt:ui:page`
- `rt:ui:focus`
- `rt:ui:layer`
- `rt:ui:modal`
- `rt:ui:browse`
- `rt:ui:authority`

## 14.2 Optional in First Version

May defer:

- `rt:ui:last_result`
- `rt:ui:page_context`
- indexed `rt:ui:browse:<panel>`

## 14.3 Conservative Rule

If exact focus or browse identity cannot yet be derived safely, the projector should publish only:

- page
- modal presence
- authority/degraded state

and omit the rest until safely derivable.

---

# 15. Deterministic Test Cases

The projector is not complete until these cases behave deterministically.

## 15.1 Page Change

Given:
- current page changes from `home` to `pota`

Expected:
- `rt:ui:page` updates only after committed cutover
- `rt:ui:focus` updates to valid page focus
- `rt:ui:layer` recomputes accordingly

## 15.2 Modal Open

Given:
- controller opens confirmation modal

Expected:
- `rt:ui:modal` exists with correct modal metadata
- `rt:ui:layer = modal`

## 15.3 Modal Close

Given:
- modal dismissed

Expected:
- `rt:ui:modal` deleted
- `rt:ui:layer` falls back to browse/default/degraded as appropriate

## 15.4 Browse Start

Given:
- browse activated on focused panel

Expected:
- `rt:ui:browse` written
- `rt:ui:layer = browse` unless modal/degraded overrides

## 15.5 Degraded Authority

Given:
- controller detects stale or degraded trust

Expected:
- `rt:ui:authority.degraded = true`
- `rt:ui:layer = degraded` unless modal/safety overrides
- stale optimistic UI projections are not left misleadingly intact

## 15.6 Result Projection

Given:
- controller emits `accepted_pending` or `rejected_post_validation`

Expected:
- `rt:ui:last_result` updated with truthful result projection
- no success projection occurs before authoritative result classification

---

# 16. Non-Negotiable Rules

1. Controller owns UI meaning.
2. Redis is the authoritative UI projection surface.
3. Projection must reflect post-controller truth, never raw input optimism.
4. Only one controller-owned writer may author `rt:ui:*` keys.
5. Missing or ambiguous state must fail closed.
6. Modal, degraded, and safety posture must override ordinary page meaning.
7. Stale projection keys must be deleted or replaced, not left misleadingly intact.
8. Renderer-only consumers may read projection but never own semantics.
9. LED derivation must consume projected controller truth rather than inventing its own UI schema.
10. Same authoritative state must always produce the same projection.

---

# 17. Completion Criteria

This specification is complete when:

1. `ui_state_projector.py` can publish stable `rt:ui:*` keys from controller-owned truth
2. page, focus, modal, browse, and authority posture are available to consumers without UI-side inference
3. console LED sender can derive state from projected UI truth rather than guessed Redis keys
4. stale and degraded conditions are represented safely
5. renderer-only UI can consume the same controller-owned semantics as the hardware feedback path

---

# Final Rule

If a browser, panel, or LED sender has to guess what the current interaction state means because the controller did not project it into Redis, the architecture is incomplete.

The controller must project UI meaning explicitly.
