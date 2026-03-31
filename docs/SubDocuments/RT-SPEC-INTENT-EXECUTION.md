# RT-SPEC-INTENT-EXECUTION.md
## RollingThunder v0.49 — Intent Execution & State Mutation Rules
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative controller-side execution model for intents after validation has succeeded.

It governs:
- how intents mutate Redis
- how action intents are dispatched
- execution ordering and atomicity
- result classification and publication
- failure handling and recovery
- concurrency and serialization
- observability

---

# 2. Core Principles

## 2.1 Execution Is State Truth Creation
Intent execution turns validated meaning into authoritative state.

## 2.2 State-First Rule
- Redis mutation defines truth
- No success may be claimed before state is committed

## 2.3 Controller Authority
- Controller executes all intents
- Services do not mutate UI state directly
- UI is read-only

## 2.4 Determinism
Same state + same intent = same result

## 2.5 Fail Closed
If execution cannot complete safely:
→ no state mutation
→ explicit failure

---

# 3. Canonical Execution Pipeline

validated_intent_received
→ execution_lock_acquired
→ state_snapshot
→ precondition_validation
→ execution_plan
→ state_mutation
→ downstream_dispatch
→ state_commit_boundary
→ result_classification
→ bus_publication
→ observability_log
→ execution_lock_release

---

# 4. Intent Classes

## State Intents
- mutate Redis
- synchronous

Examples:
- ui.page.next
- ui.focus.next
- ui.browse.delta

## Action Intents
- dispatch to services
- may be pending

Examples:
- radio.tune
- system.shutdown

## Hybrid Intents
- mutate + dispatch

---

# 5. Atomic Mutation Rules

- All Redis writes MUST be atomic
- No partial writes allowed
- Use MULTI/EXEC or Lua
- Idempotency required where applicable

---

# 6. Execution Ordering

1. validate
2. lock
3. mutate state
4. commit
5. dispatch
6. publish result
7. publish state.changed

---

# 7. Result Model

## Result Types

- accepted_completed
- accepted_completed_degraded
- accepted_noop
- accepted_pending
- rejected_post_validation
- execution_failed
- execution_timeout

## Example Result

```json
{
  "ok": true,
  "intent": "ui.page.next",
  "result": "accepted_completed",
  "timestamp": "2026-03-28T18:00:00Z"
}
```

---

# 8. State Change Publication

- state.changed AFTER commit
- no false success
- no duplicate spam

---

# 9. Action Dispatch Rules

- dispatch AFTER commit
- optional pending state allowed
- no automatic retries
- timeout required

---

# 10. Modal Execution Rules

## Create
- atomic replace

## Confirm
- execute + clear modal

## Cancel
- clear modal

---

# 11. Browse / Focus / Page Rules

## Focus
- update rt:ui:focused_panel

## Browse
- update rt:ui:browse:<panel>

## Page
- follow v0.47:
  stop → commit → start

---

# 12. Hybrid Execution Rule

state → commit → dispatch

---

# 13. Failure Handling

| Failure | Result |
|--------|-------|
| write failure | execution_failed |
| dispatch failure | degraded |
| timeout | execution_timeout |
| duplicate | noop |

---

# 14. Concurrency

- single execution lock required
- reject overlapping intents
- no hidden queues

---

# 15. Recovery

On restart:
- validate state
- repair focus
- clear unsafe modal
- reconcile services

---

# 16. Observability

Required events:
- execution.started
- execution.completed
- execution.failed
- execution.timeout
- execution.noop

---

# 17. Supporting Examples

## Example: Page Change

Input:
```json
{ "intent": "ui.page.next" }
```

Execution:
- stop old services
- update Redis:
  - current_page
  - focused_panel
  - clear modal/browse
- start new services

Result:
```json
{
  "ok": true,
  "result": "accepted_completed"
}
```

---

## Example: Radio Tune (Action)

Input:
```json
{
  "intent": "radio.tune",
  "params": { "freq_hz": 14250000 }
}
```

Execution:
- validate
- dispatch to radio service

Result:
```json
{
  "ok": true,
  "result": "accepted_pending"
}
```

Completion Event:
```json
{
  "ok": true,
  "result": "accepted_completed"
}
```

---

## Example: Rejected Intent

```json
{
  "ok": false,
  "result": "rejected_post_validation",
  "reason": "modal_blocked"
}
```

---

# 18. Completion Criteria

Execution is complete when:
- state committed OR action dispatched
- result published
- observable

---

# Final Rule

If it is not:
- committed
- observable
- deterministic

→ it did not happen.
