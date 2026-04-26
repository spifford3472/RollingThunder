# RollingThunder – Event Contract Lockdown (Authoritative)

## Status

**MANDATORY – DO NOT VIOLATE**

This document defines the enforced event-driven architecture for RollingThunder.
All future development MUST comply.

---

## Core Invariants (NON-NEGOTIABLE)

1. **Controller owns ALL state**
2. **Redis is the single source of truth**
3. **UI is renderer-only (no logic, no decisions)**
4. **All inputs flow through `rt:ui:intents`**
5. **Only the projector publishes to `rt:ui:bus`**
6. **UI reacts ONLY to `ui.projection.changed`**
7. **NO polling anywhere in the system**

---

## Event Channels (Authoritative)

### `rt:ui:intents`

**Purpose:** All control inputs

**Allowed publishers:**

* UI runtime
* Panel bridge (ESP32)
* Meshtastic bridge

**Payload example:**

```json
{
  "intent": "radio.tune",
  "params": { "freq_hz": 7218000 },
  "source": { "type": "panel_bridge", "node": "rt-controller" },
  "timestamp": 1777232642009
}
```

---

### `rt:system:bus`

**Purpose:** State change notifications ONLY

**Allowed publishers:**

* Services
* Intent worker
* State publishers

**Allowed topics:**

* `state.changed`

**Payload:**

```json
{
  "topic": "state.changed",
  "payload": {
    "keys": ["rt:controller:ui:last_result"]
  },
  "ts_ms": 1777232642013,
  "source": "ui_intent_worker"
}
```

---

### `rt:ui:bus`

**Purpose:** UI projection updates ONLY

**Allowed publisher:**

* `rt-ui-state-projector` (EXCLUSIVE)

**Allowed topic:**

* `ui.projection.changed`

**Payload:**

```json
{
  "topic": "ui.projection.changed",
  "payload": {
    "keys": ["rt:ui:focus"],
    "changed_keys": ["rt:ui:focus"],
    "deleted_keys": [],
    "ts_ms": 1777232619028
  },
  "source": "rt-ui-state-projector",
  "ts_ms": 1777232619028
}
```

---

## Forbidden Patterns (HARD ERRORS)

### ❌ Direct UI bus publishing from any service

```python
r.publish("rt:ui:bus", ...)
```

### ❌ UI consuming `rt:system:bus`

```js
subscribe("rt:system:bus")
```

### ❌ Polling loops

```js
setInterval(...)
```

### ❌ UI-triggered logic or decisions

```js
if (...) { performAction(); }
```

---

## Correct Flow (ONLY allowed flow)

```
INPUT
  ↓
rt:ui:intents
  ↓
Controller / Intent Worker
  ↓
Redis state mutation
  ↓
rt:system:bus (state.changed)
  ↓
Projector
  ↓
rt:ui:bus (ui.projection.changed)
  ↓
UI render
```

---

## Result Handling Contract

Services and workers MUST NOT publish results directly to UI.

Instead:

1. Write result to Redis:

   ```
   rt:controller:ui:last_result
   ```

2. Publish:

   ```
   state.changed
   ```

3. Projector emits UI update

---

## Enforcement Rules

* Any new service MUST:

  * Publish ONLY to `rt:system:bus`
  * NEVER publish to `rt:ui:bus`

* Any UI code MUST:

  * Use SSE only
  * React only to `ui.projection.changed`
  * NEVER poll

* Any violation is considered:
  **ARCHITECTURAL BREAKAGE**

---

## Verification Checklist

Run before any release:

```bash
# No illegal UI bus publishers
grep -R "rt:ui:bus" /opt/rollingthunder \
  | grep -v "rt-ui-state-projector.py"

# No polling
grep -R "setInterval" /opt/rollingthunder/ui

# No UI access to system bus
grep -R "rt:system:bus" /opt/rollingthunder/ui
```

---

## Final Guarantee

If this contract is followed:

* System is deterministic
* No hidden control paths
* No UI race conditions
* No polling regressions
* Fully debuggable via Redis

---

## Version

Established: RollingThunder v0.39+
Status: **LOCKED**
