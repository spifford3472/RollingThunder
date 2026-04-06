# RT-SPEC-PHASE-B-INTERACTION-SEMANTICS.md

## RollingThunder Phase B — Interaction Semantics, Browse Behavior, and Contextual OK/Cancel/Back
**Status:** Authoritative for Phase B Implementation

---

## 1. Purpose

This document locks the Phase B interaction behavior for RollingThunder after Phase A plumbing.

Phase A delivered:
- controller-owned interaction state
- controller-owned UI projection
- intent pipeline
- LED output path

Phase B focuses on **behavior correctness**:
- browse semantics
- contextual OK
- contextual Cancel/Back
- removal of renderer-side authority

---

## 2. Non-Negotiable Rules

1. Controller owns interaction meaning  
2. Redis is the source of truth  
3. UI is renderer-only  
4. Hardware is a dumb emitter/sink  
5. All control flows through intents  
6. No alternate control paths  
7. System must be deterministic  

---

## 3. Interaction Layer Priority

The controller resolves interaction state in this order:

1. modal  
2. degraded  
3. browse  
4. default  

---

## 4. Browse Ownership Model

- Browse belongs to the **focused panel**
- Entered only by controller-approved intents
- Explicit state must exist in Redis
- Browse does **not** change focus

---

## 5. Browse Entry & Movement

### Entry
- Anchor from controller-owned state (selected item or index 0)
- No renderer-derived assumptions

### Movement
- Encoder delta moves selection
- No wrapping
- No commit on move

### Home / End
- `ui.browse.home` → index 0  
- `ui.browse.end` → last index  

---

## 6. OK Resolution

`ui.ok` resolves in this order:

1. Modal confirm  
2. Browse confirm  
3. Panel default action  
4. No-op / rejected  

---

## 7. Cancel Resolution

`ui.cancel` resolves in this order:

1. Dismiss modal  
2. Exit browse  
3. No-op  

---

## 8. Back Resolution

`ui.back` resolves in this order:

1. Dismiss modal  
2. Exit browse  
3. Page previous  

---

## 9. Focus Rules

### Default
- Focus changes allowed

### Browse
- Focus locked

### Modal
- Focus locked

---

## 10. POTA Panel Rules

### Parks (`pota_parks_summary`)
- OK → commit park
- Exit browse
- Focus unchanged

### Bands (`pota_bands_summary`)
- OK → commit band
- Exit browse
- Focus → spots panel

### Spots (`pota_spots_summary`)
- OK → open controller-owned modal

---

## 11. Spots Outcome Modal

- Controller-owned
- Options:
  - cannot hear
  - worked
  - not worked
- Encoder selects
- OK confirms
- Cancel dismisses

---

## 12. UI Projection Expectations

- `rt:ui:browse` must reflect real selection
- `rt:ui:modal` must reflect real modal state
- `rt:ui:layer` must be controller-derived

---

## 13. Renderer Rule

Renderers may:
- display projected state

Renderers must NOT:
- own browse logic
- own modal logic
- move focus authoritatively

---

## 14. LED Coupling

- LEDs reflect controller truth
- Modal overrides browse
- Browse overrides default

---

## 15. Implementation Requirements

- Back button → `ui.back`
- Implement controller-owned:
  - `ui.ok`
  - `ui.cancel`
  - `ui.back`
- Lock focus during browse
- Move modal ownership to controller

---

## 16. Final Rule

If the browser determines interaction meaning, Phase B is incomplete.

The controller must define all interaction truth.
