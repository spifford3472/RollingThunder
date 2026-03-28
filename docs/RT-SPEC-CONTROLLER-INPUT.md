# RollingThunder v0.42 --- Controller Input Pipeline & Mapping Engine (Authoritative)

## Document ID

RT-SPEC-v0.42-CONTROLLER-INPUT

## Purpose

This document defines the authoritative controller-side pipeline that
converts raw input events into validated, safe, and deterministic
RollingThunder behavior.

This is a core architecture document and must remain stable over time.

------------------------------------------------------------------------

# 0. Core Principle

All input becomes intents.\
The controller is the sole authority.

There are no alternate control paths.

------------------------------------------------------------------------

# 1. End-to-End Pipeline

Every input event follows this exact deterministic path:

raw_event_received → schema_validation → panel_validation →
sequence_validation → normalization → physical_mapping →
semantic_mapping → intent_validation → execution → state_update →
bus_publish → observability_log

No stage may be skipped.

------------------------------------------------------------------------

# 2. Pipeline Stages (Detailed)

## 2.1 raw_event_received

Transport adapters normalize all incoming data into the raw event
schema.

Adapters MUST: - not inject meaning - not interpret actions - only
translate transport → schema

------------------------------------------------------------------------

## 2.2 schema_validation

Reject if: - required fields missing - schema version unsupported

Ignore unknown optional fields.

------------------------------------------------------------------------

## 2.3 panel_validation

-   register new panels
-   validate compatibility
-   track health

Reject incompatible panels.

------------------------------------------------------------------------

## 2.4 sequence_validation

Rules: - duplicate seq → drop - backward seq → drop - gaps → allowed

Never block pipeline on gaps.

------------------------------------------------------------------------

## 2.5 normalization

Attach: - rx timestamp - normalized value structure

No semantics added.

------------------------------------------------------------------------

## 2.6 physical_mapping

Maps raw input to canonical control actions.

Example: encoder.rotate +1 → browse.delta {delta:1}

Rules: - panel-specific - config-driven - no state dependency

------------------------------------------------------------------------

## 2.7 semantic_mapping

Maps canonical action → intent.

State-aware.

Priority: 1. modal 2. transient 3. browse 4. default

------------------------------------------------------------------------

## 2.8 intent_validation

Checks: - allowedIntents - safety model - motion constraints - system
state

Reject if invalid.

------------------------------------------------------------------------

## 2.9 execution

Two types:

State intents: - navigation - focus - browse

Action intents: - forwarded to services

------------------------------------------------------------------------

## 2.10 state_update

Controller updates authoritative Redis state.

------------------------------------------------------------------------

## 2.11 bus_publish

Emit events: - ui.input.accepted - ui.input.rejected - state.changed

------------------------------------------------------------------------

## 2.12 observability_log

Log: - raw event - canonical action - intent - result

------------------------------------------------------------------------

# 3. Validation Rules

Reject: - malformed - unsupported version - unknown control - disallowed
intent - safety violation

Drop: - duplicates - backward sequence

------------------------------------------------------------------------

# 4. Mapping Engine

Two-layer model:

Physical: (control_id, event_type) → canonical action

Semantic: (canonical + state) → intent

Fail closed.

------------------------------------------------------------------------

# 5. Redis Ownership

Controller owns:

-   rt:ui:current_page
-   rt:ui:focused_panel
-   rt:ui:browse:`<panel>`{=html}
-   rt:ui:modal
-   rt:panel:`<id>`{=html}:health
-   rt:input:last_accepted
-   rt:input:last_rejected

UI is read-only.

------------------------------------------------------------------------

# 6. Page Transitions

Order: 1. resolve target page 2. stop old services 3. start new services
4. update state 5. reset focus 6. reset browse 7. publish change

------------------------------------------------------------------------

# 7. ui.reload

Intent-driven.

Flow: ui.reload → controller → publish → display reload → confirm

Rules: - not browser specific - observable - controlled

------------------------------------------------------------------------

# 8. system.shutdown

Phases:

1.  request → modal
2.  confirm → execute
3.  shutdown sequence

Order: - notify nodes - stop services - shutdown display - shutdown
radio - shutdown controller last

Must never bypass confirmation.

------------------------------------------------------------------------

# 9. Failure Model

Categories: - malformed - schema mismatch - duplicate - unknown
control - mapping failure - disallowed - safety violation

Emit: ui.input.rejected

------------------------------------------------------------------------

# 10. Observability

Must track: - raw event - canonical action - intent - result - rejection
reason

------------------------------------------------------------------------

# 11. Non-Negotiable Rules

-   panel is dumb
-   UI is renderer-only
-   controller owns state
-   no bypass paths
-   shutdown requires confirmation

------------------------------------------------------------------------

# 12. Completion Criteria

-   deterministic pipeline
-   unambiguous validation
-   safe execution
-   unified control path

------------------------------------------------------------------------

# Final Rule

Every input → canonical → intent → validated → executed by controller.
