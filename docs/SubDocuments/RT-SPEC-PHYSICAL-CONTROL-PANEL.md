# RollingThunder Physical Control Panel Specification
## Version: v0.43
## Status: Authoritative

---

# 1. Purpose

This document defines the physical control panel for RollingThunder.

The panel is a hardware interface that emits input events into the
controller input pipeline defined in:

- RT-SPEC-CONTROLLER-INPUT.md
- INTENTS.md

The panel contains:

- 10 momentary buttons (with LEDs)
- 1 rotary encoder with push button

The panel is:

- stateless
- logic-free
- an event emitter only

---

# 2. System Constraints (Non-Negotiable)

- Redis is the source of truth
- Controller owns all state
- UI is renderer-only
- All control paths flow through intents
- No logic exists in hardware
- System must remain deterministic and safe for mobile use

---

# 3. Control Philosophy

The control system is based on three layers:

1. Awareness (LED state)
2. Navigation (encoder + blue buttons)
3. Action (green/red/yellow/white buttons)

---

# 4. Color Semantics (Locked)

| Color | Meaning |
|------|--------|
| Red | Cancel / Danger / System |
| Green | Confirm / Execute |
| Blue | Navigation |
| Yellow | Mode / Modify |
| White | Utility |

These meanings MUST NOT change.

---

# 5. Control Roles

## Blue (Navigation)
- BACK / EXIT
- PAGE / NEXT VIEW

## Green (Primary Action)
- OK / CONFIRM
- CONTEXT ACTION

## Red (Safety / System)
- CANCEL / ABORT
- SYSTEM / POWER

## Yellow (Mode / Secondary)
- MODE / FILTER
- MARK / HOLD

## White (Utility)
- INFO / DETAILS
- AUX / USER FUNCTION

---

# 6. Encoder Behavior

- Rotate → navigation / adjustment
- Press → select / confirm
- Hold → contextual actions

---

# 7. LED Behavior Model

LEDs represent system state, not button presses.

## Green
- ON → action available
- BLINK → confirmation required

## Red
- ON → dangerous state
- BLINK → destructive confirmation required

## Blue
- ON → active page/context

## Yellow
- ON → mode active
- BLINK → temporary state

## White
- ON → info available
- BLINK → new data

---

# 8. Modal Interaction

- Encoder → selection
- Green → confirm
- Red → cancel
- Blue → exit
- Yellow → secondary option

---

# 9. Safety Model

- Red always cancels
- Green always confirms
- Navigation is always non-destructive
- System must be operable without visual attention

---

# 10. Design Goals

- One-hand operation
- Eyes-off usability
- Deterministic behavior
- Consistent with professional radio control heads
