# RT-SPEC-INTERACTION-STATE.md
## RollingThunder v0.48 — Interaction State Machine & Context Resolution
## Status: Authoritative (Ultra)

---

# 1. Purpose

This document defines the authoritative controller-side interaction state machine for RollingThunder.

This specification governs how all canonical actions are interpreted into intents based on controller-owned context.

It defines:

- full interaction state model
- strict priority resolution
- Redis-derived context authority
- deterministic state transitions
- semantic mapping integration
- transient and modal lifecycle
- ambiguity elimination
- failure handling and recovery

This document MUST be implemented exactly as written.

---

# 2. Core Principles

## 2.1 State Is the Only Source of Meaning

Canonical actions are meaningless without interaction state.

Meaning = canonical_action + interaction_state

---

## 2.2 Controller Is the Sole Authority

- interaction state exists only in controller-owned Redis keys
- UI and panel are stateless
- no alternate interpretation paths exist

---

## 2.3 Determinism

Given identical:

- Redis state
- canonical action

The resulting intent MUST be identical.

---

## 2.4 Priority Enforcement

The interaction state machine MUST enforce:

modal > transient > browse > default

---

## 2.5 Fail Closed

If a valid single interaction state cannot be derived:

→ input MUST be rejected

---

# 3. Canonical Interaction State Model

## 3.1 State Layers

The system defines four mutually exclusive interpretation layers:

1. MODAL
2. TRANSIENT
3. BROWSE
4. DEFAULT

FOCUS is a property, not a layer.

---

## 3.2 State Definitions

### DEFAULT

Conditions:

- no modal
- no transient
- no browse

Behavior:

- NAV_DELTA → focus navigation
- full page allowedIntents apply

---

### BROWSE

Conditions:

- rt:ui:browse:<panel> exists
- panel matches focused_panel
- panel exists on current_page

Behavior:

- NAV_DELTA → ui.browse.delta
- input scoped to panel content

---

### TRANSIENT

Conditions:

- controller-owned transient key exists
- not expired

Behavior:

- overrides browse/default
- defines temporary mapping rules

---

### MODAL

Conditions:

- rt:ui:modal exists
- valid structure
- not stale

Behavior:

- overrides ALL other states
- restricts all input to modal semantics

---

## 3.3 State Invariants

- only one effective state layer may be active
- modal is exclusive
- browse is single-panel scoped
- transient must be bounded
- focus must always be valid

---

# 4. State Priority Model

## 4.1 Priority Order (Non-Negotiable)

1. MODAL
2. TRANSIENT
3. BROWSE
4. DEFAULT

---

## 4.2 Resolution Algorithm

The controller MUST compute effective state using:

1. validate modal
2. if valid → MODAL
3. validate transient
4. if active → TRANSIENT
5. validate browse
6. if active → BROWSE
7. else → DEFAULT

---

## 4.3 Conflict Detection

If:

- multiple browse keys active
- invalid modal present
- transient overlaps modal

→ reject input with reason: state_conflict

---

# 5. Redis Context Authority

## 5.1 Required Keys

- rt:ui:current_page
- rt:ui:focused_panel
- rt:ui:modal
- rt:ui:browse:<panel>

---

## 5.2 Validation Rules

### current_page

MUST:

- exist
- match config
- be navigable

Else:

→ recover or reject

---

### focused_panel

MUST:

- exist on current page
- be interactable

Else:

→ repair to page default

---

### browse:<panel>

Valid if:

- panel == focused_panel
- panel exists on page
- data valid

Else:

→ clear

---

### modal

Valid if:

- structure valid
- action set defined
- not stale

Else:

→ block interaction until cleared

---

## 5.3 Missing State Handling

| Key | Behavior |
|-----|--------|
| current_page missing | recover to default |
| focused_panel invalid | repair |
| browse invalid | clear |
| modal invalid | block |

---

# 6. State Transition Model

## 6.1 Transition Table

| From | To | Trigger |
|------|----|--------|
| DEFAULT | BROWSE | ui.ok |
| BROWSE | DEFAULT | ui.back / ui.cancel |
| ANY | MODAL | controller sets modal |
| MODAL | DEFAULT | confirm/cancel |
| ANY | TRANSIENT | controller sets transient |
| TRANSIENT | DEFAULT | timeout/complete |

---

## 6.2 Transition Rules

- transitions MUST be deterministic
- transitions MUST be observable
- transitions MUST not overlap
- transitions MUST not depend on timing variance

---

## 6.3 Ordering Rules

When multiple transitions occur:

1. modal resolution
2. transient resolution
3. browse resolution
4. focus repair

---

# 7. Semantic Mapping Integration

## 7.1 Mapping Order

Mapping MUST follow:

modal → transient → browse → default

---

## 7.2 Execution Rule

- only one mapping layer executes
- lower layers ignored
- no fallback chaining allowed

---

## 7.3 Illegal Mapping

If mapping requires invalid state:

→ reject input

---

# 8. Transient State Model

## 8.1 Definition

Transient state is:

- controller-owned
- explicitly stored
- time-bound

---

## 8.2 Lifecycle

- created by controller
- active until:
  - timeout
  - completion
  - cancel

---

## 8.3 Timeout Rules

- MUST be bounded
- MUST auto-expire
- expiration MUST emit observability event

---

## 8.4 Interaction Rules

- transient cannot override modal
- transient overrides browse/default

---

# 9. Modal Authority Model

## 9.1 Exclusivity

Only one modal may exist.

---

## 9.2 Blocking Rules

While modal active:

- only modal intents allowed
- all others rejected

---

## 9.3 Replacement

New modal replaces existing modal atomically.

---

## 9.4 Exit Guarantee

Modal MUST exit via:

- confirm
- cancel
- forced clear

---

# 10. Focus Model

## 10.1 Rules

- focus MUST always exist
- focus MUST be valid

---

## 10.2 Repair Rule

If invalid:

→ set to page default

---

## 10.3 Interaction

- browse tied to focused panel
- changing focus exits browse

---

# 11. Browse Model

## 11.1 Entry

Triggered by:

ui.ok on browsable panel

---

## 11.2 Exit

Triggered by:

- ui.back
- ui.cancel
- page transition

---

## 11.3 Reset Rule

Browse MUST be cleared on page transition.

---

# 12. allowedIntents Integration

## 12.1 State Impact

| State | Allowed Intents |
|------|----------------|
| MODAL | restricted |
| TRANSIENT | restricted |
| BROWSE | limited |
| DEFAULT | full page |

---

## 12.2 Enforcement

Validation MUST use:

- current_page
- effective interaction state

---

## 12.3 No Stale Permissions

Permissions change immediately on state change.

---

# 13. Failure and Edge Cases

## 13.1 Missing Context

→ reject input

---

## 13.2 Invalid Focus

→ repair

---

## 13.3 Stale Browse

→ clear

---

## 13.4 Invalid Modal

→ block interaction

---

## 13.5 State Conflict

→ reject input

---

## 13.6 Controller Restart

Controller MUST:

1. validate config
2. restore current_page
3. repair focus
4. clear unsafe modal
5. clear browse if uncertain

---

# 14. Observability

## 14.1 Required Events

- ui.interaction.state.changed
- ui.interaction.state.rejected
- ui.interaction.state.recovered

---

## 14.2 Logging Requirements

MUST log:

- canonical action
- resolved state
- resulting intent
- rejection reason
- recovery actions

---

# 15. Completion Criteria

System is valid when:

1. exactly one effective state exists
2. mapping produces one intent
3. no ambiguity exists
4. invalid state is rejected or repaired
5. system is deterministic

---

# 16. Non-Negotiable Rules

1. State defines meaning
2. Priority is absolute
3. Redis is source of truth
4. Modal overrides all
5. No ambiguity allowed
6. Fail closed always
7. No fallback interpretation
8. No hidden state

---

# Final Rule

If the controller cannot derive exactly one valid interaction state:

→ the input MUST be rejected
