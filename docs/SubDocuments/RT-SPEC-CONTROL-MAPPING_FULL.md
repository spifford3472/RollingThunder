# RT-SPEC-CONTROL-MAPPING.md
## RollingThunder v0.44 — Control Mapping to Input Pipeline
## Status: Authoritative

---

# 1. Purpose

This document defines how physical control panel inputs are transformed into intents
through the controller input pipeline.

This specification strictly follows:

- RT-SPEC-CONTROLLER-INPUT.md
- RT-SPEC-PHYSICAL-CONTROL-PANEL.md
- INTENTS.md

This document defines:

- Input event schema
- Control → canonical action mapping
- Canonical action → intent mapping
- Context usage rules
- Safety constraints
- Failure handling

---

# 2. Core Principles

- All inputs become intents
- Controller is the sole authority
- Panel is stateless and emits only events
- Mapping must be deterministic
- Same input + same context = same intent
- Fail closed on all errors

---

# 3. Input Event Model

## 3.1 Canonical Event Schema

All panel inputs MUST be normalized into:

{
  "event_id": "<uuid>",
  "panel_id": "<string>",
  "control_id": "<string>",
  "event_type": "<string>",
  "value": <number|null>,
  "timestamp": "<iso8601>",
  "seq": <integer>
}

## 3.2 Event Types

Buttons:
- press
- hold
- repeat (optional)

Encoder:
- rotate
- press
- hold

## 3.3 Rules

- No semantics in event
- No interpretation in panel
- Value only used for encoder delta
- Timestamp added at controller ingress

---

# 4. Canonical Actions (Physical Mapping Output)

Physical mapping produces canonical actions:

- NAV_DELTA (value: +1 / -1)
- SELECT
- BACK
- CANCEL
- PRIMARY
- SECONDARY
- PAGE_NEXT
- PAGE_PREV
- MODE_TOGGLE
- INFO

Rules:

- Stateless
- No context used
- Deterministic mapping

---

# 5. Control → Canonical Action Mapping

## 5.1 Blue Buttons (Navigation)

BACK button:
- press → BACK
- hold → PAGE_PREV

PAGE button:
- press → PAGE_NEXT
- hold → PAGE_PREV

## 5.2 Green Button (Primary)

- press → PRIMARY
- hold → PRIMARY

## 5.3 Red Button (Safety)

- press → CANCEL
- hold → CANCEL

## 5.4 Yellow Buttons (Mode)

- press → MODE_TOGGLE
- hold → SECONDARY

## 5.5 White Buttons (Utility)

- press → INFO
- hold → SECONDARY

## 5.6 Encoder

Rotate:
- +1 → NAV_DELTA {+1}
- -1 → NAV_DELTA {-1}

Press:
- press → SELECT

Hold:
- hold → SECONDARY

---

# 6. Context Model

Context is read from Redis:

- rt:ui:current_page
- rt:ui:focused_panel
- rt:ui:browse:<panel>
- rt:ui:modal

Rules:

- Mapping reads context only
- Mapping does not store state
- Controller is source of truth
- No caching of context in mapping layer

---

# 7. Semantic Mapping (Canonical → Intent)

Priority order:

1. modal
2. transient
3. browse
4. default

---

## 7.1 Modal Context

NAV_DELTA → ui.browse.delta
SELECT → ui.ok
PRIMARY → ui.ok
CANCEL → ui.cancel
BACK → ui.back
SECONDARY → ui.action.secondary

---

## 7.2 Browse Context

NAV_DELTA → ui.browse.delta
SELECT → ui.ok
PRIMARY → ui.ok
BACK → ui.back
CANCEL → ui.cancel

---

## 7.3 Default Context

NAV_DELTA:
- +1 → ui.focus.next
- -1 → ui.focus.prev

SELECT → ui.ok
PRIMARY → ui.ok
BACK → ui.back
CANCEL → ui.cancel
PAGE_NEXT → ui.page.next
PAGE_PREV → ui.page.prev
INFO → ui.action.secondary
MODE_TOGGLE → ui.action.secondary
SECONDARY → ui.action.secondary

---

## 7.4 System Actions (Gated)

Red hold with active confirmation modal:
→ system.shutdown

White hold in maintenance-enabled context:
→ ui.reload

---

# 8. Intent Validation

All intents must pass:

- allowedIntents gating
- safety validation
- system state validation

Invalid intents MUST be rejected.

---

# 9. Safety Rules

- Red always cancels (ui.cancel)
- Green confirms only via ui.ok
- system.shutdown requires confirmation modal
- No destructive action without modal
- Navigation intents are always safe
- No hidden or implicit destructive actions

---

# 10. Hold and Repeat Behavior

- Hold generates distinct event_type
- Hold MUST NOT bypass safety rules
- Repeat allowed ONLY for NAV_DELTA
- Repeat MUST be bounded and cancellable
- No repeat for SELECT, PRIMARY, CANCEL

---

# 11. Failure Handling

## 11.1 Unknown Control
→ reject event

## 11.2 Unknown Context
→ no action (fail closed)

## 11.3 Blocked Intent
→ emit ui.input.rejected

## 11.4 Missing State
→ reject safely

## 11.5 Mapping Failure
→ reject

## 11.6 Controller Not Responding
→ no execution
→ no state mutation

---

# 12. Observability

System MUST log:

- raw event
- canonical action
- resolved intent
- validation result
- rejection reason (if applicable)

---

# 13. Determinism Requirements

- No randomness
- No timing-dependent branching
- No hidden state
- No panel-side interpretation
- Mapping must be reproducible

---

# 14. Completion Criteria

- deterministic mapping
- strict INTENTS.md compliance
- alignment with controller pipeline
- consistent with physical panel semantics
- safe for mobile operation

---

# Final Rule

All physical input must resolve to a valid intent through the controller pipeline, or be safely rejected.
