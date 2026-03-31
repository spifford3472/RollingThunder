# RT-SPEC-LED-STATE-MODEL.md
## RollingThunder v0.45 — LED State Model & Feedback Loop
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative LED feedback model for the RollingThunder physical control panel.

It specifies:

- controller-owned LED state semantics
- Redis-driven feedback rules
- operator awareness behavior
- LED priority and conflict resolution
- deterministic feedback after validation and execution
- safe degradation behavior

This document does **not** define:

- electrical wiring
- firmware implementation details
- browser/UI rendering
- timing values at the hardware driver level

This specification strictly follows:

- INTENTS.md
- RT-SPEC-CONTROLLER-INPUT.md
- RT-SPEC-PHYSICAL-CONTROL-PANEL.md
- RT-SPEC-CONTROL-MAPPING.md

---

# 2. Core Principles

## 2.1 LEDs Reflect State, Not Input

LEDs are a view of authoritative controller state.

They MUST NOT:

- indicate a raw button press merely because it occurred
- predict an action before validation
- imply success before state has actually changed
- depend on hardware-side logic

LEDs MUST reflect only post-controller state.

---

## 2.2 Controller Owns All LED Meaning

The controller is the sole authority for LED meaning and LED state derivation.

The panel is a dumb event emitter and dumb LED sink.

The panel MUST NOT:

- infer intent meaning
- decide LED patterns locally
- retain semantic state across reconnects
- synthesize fallback behaviors beyond transport-safe defaults

---

## 2.3 Redis Is the Source of Truth

LED behavior is derived from controller-owned Redis state.

The LED pipeline is:

Redis state → controller LED derivation → canonical LED output state → panel transport message → physical LED display

---

## 2.4 Determinism

The same authoritative Redis state MUST always produce the same LED state.

No randomness, hidden memory, or timing-dependent interpretation is allowed.

---

## 2.5 Safety First

When state is ambiguous, stale, degraded, or missing, LED behavior MUST fail safely and clearly.

LEDs must be trustworthy.

---

# 3. Functional Role of LEDs

LEDs are the operator awareness system.

They exist to communicate:

- what mode the controls are currently in
- whether confirmation is available
- whether cancellation is available
- whether a page or context is active
- whether a temporary mode is engaged
- whether information is waiting
- whether the system is degraded or requires caution

The operator must be able to infer the current interaction posture without needing to study the screen.

---

# 4. LED Architecture

## 4.1 Closed Feedback Loop

The authoritative control loop is:

input → controller pipeline → intent validation → execution → Redis state update → LED derivation → panel LED update

This is the only valid feedback loop.

LEDs MUST be updated from the resulting state after validation and execution.

They MUST NOT be updated directly from:

- raw events
- pre-validation canonical actions
- optimistic assumptions
- transport-level acknowledgement alone

---

## 4.2 LED Update Domains

LED updates are derived from four logical domains:

1. **interaction state**
   - page
   - focus
   - browse state
   - modal state

2. **system state**
   - controller health
   - panel health
   - degraded/stale state

3. **service or page capability state**
   - whether primary action is currently valid
   - whether secondary mode is active
   - whether info is available

4. **recent result state**
   - accepted action reflected through resulting state
   - rejected input reflected only through controller-owned error/result state

---

# 5. Canonical LED State Model

## 5.1 LED Output Schema

The controller MUST derive a canonical LED output object for every LED-bearing control.

Canonical schema:

```json
{
  "panel_id": "<string>",
  "revision": <integer>,
  "generated_at": "<iso8601>",
  "source_state_epoch_ms": <integer>,
  "leds": {
    "<control_id>": {
      "mode": "off|on|blink_slow|blink_fast|pulse",
      "reason": "<stable_reason_code>",
      "priority": "modal|alert|system|context|normal|degraded",
      "color_semantic": "red|green|blue|yellow|white",
      "context_ref": {
        "page": "<string|null>",
        "focused_panel": "<string|null>",
        "modal": "<string|null>",
        "state_key": "<string|null>"
      }
    }
  }
}
```

---

## 5.2 Allowed LED Modes

Each LED MUST support the following semantic modes:

- `off`
- `on`
- `blink_slow`
- `blink_fast`
- `pulse`

### Meaning

#### off
No current state claim is being made for that control.

#### on
Stable active meaning. The control currently has an available or active role.

#### blink_slow
Attention required for a stable but important condition.
Typically used for confirmation-required or new-info states.

#### blink_fast
Urgent condition requiring immediate recognition.
Typically used for destructive confirmation or degraded/system-fault conditions.

#### pulse
Soft contextual emphasis without implying urgency.
Used sparingly for “available but not urgent” awareness.

---

## 5.3 Use of Pulse

`pulse` is optional but justified.

It exists to distinguish:

- a stable active state (`on`)
- from an attention-seeking but non-urgent state (`pulse`)
- without escalating to blink semantics reserved for stronger meaning

`pulse` SHOULD be used only when it improves clarity and reduces overuse of blinking.

---

# 6. Controller-Owned Sources of LED Truth

The controller SHOULD derive LED state from the following Redis-owned keys or equivalent controller-owned state projections.

## 6.1 Primary Interaction State

- `rt:ui:current_page`
- `rt:ui:focused_panel`
- `rt:ui:browse:<panel>`
- `rt:ui:modal`

## 6.2 Input/Result State

- `rt:input:last_accepted`
- `rt:input:last_rejected`
- `rt:input:last_result`
  - if this is introduced as a controller-owned consolidation key, it MUST remain derived state only

## 6.3 System / Health State

- `rt:system:health`
- `rt:panel:<id>:health`
- controller-maintained panel link / freshness state

## 6.4 Radio / Page Capability State

- `rt:radio:state`
- page-specific controller-owned state that determines whether a primary action is currently valid
- page-specific controller-owned state that determines whether info exists
- page-specific controller-owned state that determines whether a temporary mode is active

---

# 7. LED Derivation Rules

## 7.1 General Rule

LED derivation MUST be state-based and declarative.

The controller computes LED state from current context, not from the event that most recently occurred.

---

## 7.2 No Direct Press Coupling

A button press MUST NOT directly turn its own LED on.

That LED may change only if the press causes a valid intent that results in a state transition which then changes the LED model.

Example:

- operator presses green
- controller validates `ui.ok`
- controller updates modal or browse state
- LED changes only after the resulting state is written

---

## 7.3 No Anticipatory Success

If an action has not yet succeeded, LEDs MUST NOT present it as succeeded.

Examples:

- pressing green does not immediately turn green ON unless the resulting state now marks confirmable success or stable action availability
- pressing red during shutdown confirmation does not extinguish danger unless the modal is actually dismissed

---

## 7.4 Reflect Current Interaction Mode

LEDs MUST emphasize the current interaction layer in this order:

1. modal
2. destructive/alert/safety state
3. browse mode
4. page/context state
5. passive info state

---

# 8. Behavior by Color Group

The color semantics defined in the physical panel specification are locked and MUST NOT change.

---

## 8.1 Green — Confirm / Execute

Green communicates that a valid affirmative action exists in the current controller state.

### Green OFF

Use when:

- no confirmable action is currently valid
- the current state does not permit `ui.ok`
- the system is degraded such that confirmation should not be encouraged

Meaning:

- nothing affirmative is currently available
- do not press expecting execution

### Green ON

Use when:

- a stable, valid primary action is available now
- the focused context or modal has a current confirm path
- `ui.ok` would be accepted under current state

Examples:

- a modal selection is valid and can be confirmed
- a focused browse item is actionable
- a page action is currently available and allowed

### Green BLINK_SLOW

Use when:

- the current state requires explicit operator confirmation to proceed
- the operator is in a confirmation posture but not a destructive one

Examples:

- modal confirmation pending for a non-destructive action
- secondary confirmation stage for a safe context action

### Green BLINK_FAST

Reserved and generally discouraged.

Use only if a future action requires urgent positive acknowledgement without danger semantics.
In normal operation this SHOULD NOT be used.

### Green PULSE

Use when:

- a primary action is available but not central to the current attention hierarchy
- a non-urgent “ready” signal improves awareness

This is optional and page-dependent.

---

## 8.2 Red — Danger / Cancel / System

Red communicates cancellation, danger posture, or system-level caution.

### Red OFF

Use when:

- no cancel path is currently emphasized
- no danger or destructive confirmation exists
- no system caution condition is active

Red MAY still be physically pressable for `ui.cancel`, but LED OFF means no elevated danger/system warning is being signaled.

### Red ON

Use when:

- a cancel/abort path is active in current interaction context
- a modal is active and can be dismissed
- a system caution state exists but is stable, not urgent

Examples:

- modal open with cancel available
- browse exit path is currently important
- controller degraded but not yet fault-urgent

### Red BLINK_SLOW

Use when:

- a dangerous state is present and requires attention
- a controlled system action is armed but not yet in final destructive confirmation

Examples:

- system action workflow entered
- persistent degraded state requiring caution

### Red BLINK_FAST

Use when:

- destructive confirmation is explicitly required now
- the operator is at the final point where cancellation is critical
- a serious system fault or stale-state hazard is present

Examples:

- shutdown confirmation modal active and armed
- controller authority degraded enough that trust in normal operation is reduced

### Red PULSE

Use sparingly for non-urgent system awareness where steady ON is too heavy and blink is too strong.
Normally `on` or `blink_*` is preferred for red semantics.

---

## 8.3 Blue — Navigation

Blue communicates navigation layer and spatial context.

### Blue OFF

Use when:

- that navigation role is not currently relevant
- the corresponding page/focus navigation path is inactive or suppressed by modal priority

### Blue ON

Use when:

- the blue control’s navigation role is currently active and available
- it indicates current page, context, or navigational availability

Examples:

- current page has page navigation available
- back/exit path is valid in current state
- active context should reinforce navigational orientation

### Blue BLINK_SLOW

Use when:

- a page transition has completed and a temporary re-orientation cue is helpful
- focus context changed and navigation awareness should be briefly reinforced

This SHOULD be brief and controller-driven, not event-driven optimism.

### Blue BLINK_FAST

Normally not used.
Reserved for rare navigation-critical conditions.

### Blue PULSE

Use when:

- the page or navigation context is active but not dominant
- a softer “you are here” cue is beneficial

Recommended use:

- current page indicator button
- currently active navigation context anchor

---

## 8.4 Yellow — Mode / Modify

Yellow communicates temporary mode, modification state, or alternate control posture.

### Yellow OFF

Use when:

- no mode or modify state is active
- no alternate function is currently engaged

### Yellow ON

Use when:

- a stable mode is active
- a secondary behavior layer is enabled
- a filter, mark, hold, or modify state is currently latched in controller state

### Yellow BLINK_SLOW

Use when:

- a temporary mode is active
- a mode is armed, transitional, or should be noticeable until resolved

Examples:

- transient mode layer active
- modify context active for a limited duration

### Yellow BLINK_FAST

Generally avoid.
Use only when a temporary mode is active and immediate operator awareness is critical.

### Yellow PULSE

Use when:

- an alternate mode is available or suggested but not active
- mode awareness is helpful without demanding attention

---

## 8.5 White — Info / Utility

White communicates information availability, utility context, or supplemental awareness.

### White OFF

Use when:

- no info or utility state is currently relevant
- there is nothing new or actionable in the information layer

### White ON

Use when:

- information is available now
- details/help/utility action can be invoked meaningfully
- a utility function is currently available in the page context

### White BLINK_SLOW

Use when:

- new information has arrived
- data freshness changed in a way worth surfacing
- a utility action is newly relevant

Examples:

- new data available for the current page
- a status/details view contains unseen information

### White BLINK_FAST

Avoid for routine info.
Reserve only for informational conditions elevated by safety or system significance.

### White PULSE

Use when:

- info exists but is passive
- utility availability should be discoverable without being intrusive

---

# 9. Context Awareness Model

## 9.1 Default Context

When no modal is active and browse mode is not active:

- blue communicates page/focus/navigation orientation
- green communicates whether current focus supports confirmation/action
- red communicates cancel/system caution only if relevant
- yellow communicates mode layer state
- white communicates information availability

This is the base operating posture.

---

## 9.2 Browse Context

When browse mode is active for the focused panel:

- blue MUST reinforce navigational role
- green MUST indicate whether the current browsed item is valid for `ui.ok`
- red SHOULD indicate escape/cancel availability if browse exit is important
- yellow MAY indicate alternate browse mode or modifier state
- white MAY indicate additional details for the highlighted item

Browse mode should feel distinct from focus-only navigation.

---

## 9.3 Modal Context

Modal state overrides normal page context.

When `rt:ui:modal` is active:

- modal semantics take priority over normal page/page-focus meaning
- green reflects modal confirmability
- red reflects modal cancellation/danger posture
- blue may indicate modal navigation or exit if defined by the modal model
- yellow may indicate secondary modal option only if controller state explicitly supports it
- white may indicate contextual information, but MUST NOT distract from confirm/cancel semantics

The operator must be able to recognize modal state primarily through green/red posture.

---

## 9.4 Page Transition Awareness

After a completed page change:

- blue SHOULD provide a brief contextual orientation cue
- other LEDs MUST quickly settle into the new page-derived stable state

This cue MUST result from completed state transition, not from requested page transition.

---

## 9.5 Loading / Busy / Awaiting State

If the controller uses an explicit state that a page or workflow is awaiting service completion:

- LEDs MUST NOT claim success
- stable navigation should remain truthful
- green SHOULD be OFF unless a valid confirm action still exists
- white MAY pulse or slow blink if useful for passive “info pending” awareness
- red MAY indicate caution only if cancellation or degraded behavior is truly relevant

Busy is not success.

---

# 10. Feedback Loop Rules

## 10.1 Authoritative Loop

The only valid model is:

1. operator input received
2. controller validates event and maps intent
3. controller validates intent against safety/context
4. controller executes or rejects
5. controller updates authoritative state
6. LED derivation runs from authoritative state
7. panel LEDs update

---

## 10.2 Accepted Input

For accepted input:

- LEDs change only if authoritative state changed
- if no state changed, LEDs may remain unchanged
- acceptance itself is not enough to alter LED state

---

## 10.3 Rejected Input

For rejected input:

- LEDs MUST NOT mimic the requested but rejected action
- LEDs MAY reflect controller-owned rejection/error state if such state is published authoritatively
- absent a dedicated rejection state, LEDs remain in the truthful pre-existing state

This prevents lying.

---

## 10.4 Service Actions

For action intents forwarded to services:

- LEDs MUST NOT imply downstream success until controller-owned state shows the new truth
- if controller publishes an intermediate pending state, LEDs may reflect that pending state truthfully
- if no pending state exists, LEDs remain on prior truth until a new state is confirmed

---

# 11. Safety Feedback Model

## 11.1 Destructive Actions Pending

When a destructive or system-level action is awaiting confirmation:

- red MUST escalate above normal context
- red SHOULD blink fast at final destructive confirmation stage
- green SHOULD blink slow only if confirm is valid and armed
- all non-essential LED meanings SHOULD yield to this posture

This creates unmistakable confirm/cancel polarity.

---

## 11.2 Confirmation Required

For non-destructive confirmation:

- green SHOULD blink slow
- red SHOULD be ON if cancel is available
- blue/yellow/white SHOULD be subordinate to the confirmation posture

---

## 11.3 Rejected Unsafe Action

When an unsafe or blocked input is rejected:

- the requested LED state MUST NOT appear
- controller MAY expose a transient rejection state
- if rejection state exists, red SHOULD pulse or blink slow briefly to indicate “not allowed”
- if no rejection state exists, keep LEDs unchanged rather than lying

Rule:

It is better to show no new LED effect than a misleading one.

---

## 11.4 System Error / Degraded Control

When the controller detects degraded trust:

- red SHOULD indicate caution
- white SHOULD NOT continue signaling ordinary informational optimism as if nothing is wrong
- green SHOULD turn OFF for actions that can no longer be validated safely

The system must visually narrow into a safe posture.

---

# 12. Timing and Priority Rules

## 12.1 Priority Order

LED derivation MUST resolve competing meanings in this order:

1. destructive confirmation / critical safety
2. modal interaction
3. system degraded / stale / fault
4. browse state
5. page navigation/context
6. mode/modify state
7. info/utility state
8. passive/default off

Higher-priority state MUST override lower-priority cosmetic meaning.

---

## 12.2 Conflict Resolution

If multiple meanings compete for the same LED:

- choose the highest-priority truthful meaning
- never combine incompatible claims in one LED
- never alternate between meanings in a way that obscures interpretation

Example:

If red is both “cancel available” and “shutdown confirmation armed,” it MUST display the shutdown confirmation state.

---

## 12.3 Blink Semantics

Blink speed is semantic, not implementation-specific in this document.

### blink_slow
Means:

- confirmation pending
- temporary mode active
- new data awaiting review
- attention needed, but not urgent danger

### blink_fast
Means:

- destructive confirmation
- critical caution
- trust-reducing system fault

Hardware timing values may be standardized later, but meaning is locked here.

---

## 12.4 Persistence Rules

A transient attention indication SHOULD end when:

- the underlying state clears
- a higher-priority state replaces it
- the controller expires the transient state by explicit rule

A blink or pulse MUST NOT continue after the authoritative reason is gone.

---

# 13. Failure Handling

## 13.1 Missing Redis State

If required state for LED derivation is missing:

- fail closed
- do not invent meaning
- green SHOULD be OFF
- yellow SHOULD be OFF unless a stable mode state is still known valid
- white SHOULD be OFF unless safe informational truth remains
- red SHOULD indicate degraded/stale caution if controller can determine missing-state risk

Unknown must not appear as normal.

---

## 13.2 Controller Degraded

If controller health is degraded but still able to emit LED state:

- red SHOULD be ON or blink depending on severity
- green SHOULD only remain active for operations still safe to validate
- page/context indicators SHOULD reduce to minimal truthful posture

---

## 13.3 Controller Authority Lost

If panel transport remains alive but controller authority is effectively unavailable:

- the panel MUST NOT keep stale “normal” LED meaning indefinitely
- controller-side freshness logic SHOULD drive a degraded state before authority is considered lost
- once stale threshold is crossed, LEDs SHOULD collapse to safe degraded signaling rather than routine context signaling

Recommended posture:

- red blink fast or slow depending on severity
- all non-essential LEDs off

---

## 13.4 Panel Disconnect / Reconnect

On disconnect:

- controller marks panel health degraded/offline
- no attempt is made to preserve semantic meaning on absent hardware

On reconnect:

- panel registers through normal controller pipeline rules
- controller re-sends full canonical LED state snapshot
- no semantic state is recovered from the panel itself

The panel is never authoritative.

---

## 13.5 Stale State Detected

If the controller detects state staleness relevant to safe operation:

- stale awareness MUST override passive informational cues
- red SHOULD indicate degraded trust
- green SHOULD be suppressed for actions no longer safely trustworthy

---

# 14. Recommended Controller LED Derivation Inputs

The following derived booleans are recommended for implementation clarity.
They are not panel state; they are controller-computed inputs to the LED model.

- `is_modal_active`
- `is_modal_confirmable`
- `is_destructive_confirmation`
- `is_browse_active`
- `is_primary_action_available`
- `is_cancel_emphasized`
- `is_page_nav_available`
- `is_back_available`
- `is_mode_active`
- `is_temp_mode_active`
- `is_info_available`
- `has_new_info`
- `is_system_degraded`
- `is_state_stale`
- `is_panel_healthy`
- `is_controller_authoritative`

These MUST be derived from controller-owned truth and MUST NOT be panel-maintained.

---

# 15. Per-Color Behavioral Summary

## Green

- `off` = no valid affirmative action now
- `on` = valid stable confirm/execute action available
- `blink_slow` = confirmation required
- `pulse` = non-urgent ready state

## Red

- `off` = no elevated cancel/danger/system warning signaled
- `on` = cancel path or stable caution exists
- `blink_slow` = important caution / armed system workflow
- `blink_fast` = destructive confirmation or critical trust issue

## Blue

- `off` = navigation cue not active
- `on` = current page/context/navigation role active
- `blink_slow` = transient orientation cue after completed context change
- `pulse` = passive current-context awareness

## Yellow

- `off` = no modify/mode state active
- `on` = stable mode active
- `blink_slow` = temporary mode active
- `pulse` = alternate mode available/suggested

## White

- `off` = no relevant info/utility state
- `on` = info/utility available
- `blink_slow` = new info waiting
- `pulse` = passive info available

---

# 16. Non-Negotiable Rules

1. LEDs reflect controller-owned state, never raw input.
2. LEDs MUST NOT anticipate success.
3. Panel hardware contains no semantic logic.
4. Redis-backed controller state is authoritative.
5. Modal and safety states override normal page meaning.
6. Destructive confirmation must be unmistakable.
7. Rejected input must not produce misleading success feedback.
8. Stale or degraded state must fail clearly and safely.
9. Same state must always produce the same LED output.
10. LEDs are operational feedback, not decoration.

---

# 17. Completion Criteria

This specification is complete when implementation can unambiguously derive:

- LED output schema
- Redis-to-LED state mapping
- modal/safety priority handling
- post-validation feedback behavior
- degraded and stale-state fallback behavior
- deterministic per-color semantics aligned with the physical panel

---

# Final Rule

The physical panel LEDs are a deterministic, controller-derived awareness layer.

They do not show what the operator tried to do.
They show what the system truthfully is.
