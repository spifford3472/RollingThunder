# RollingThunder — Presence Policy (Authoritative) #

This document defines how RollingThunder determines node presence and how the system must behave when nodes become stale or offline.

It exists to prevent:
- hidden “it depends” behavior
- UI drift when a node is missing
- noisy alert storms
- unsafe assumptions about system readiness

If presence-derived behavior changes but this document is not updated, the change is considered incomplete.

## 1. Scope ##
Presence applies to all RollingThunder nodes:
- rt-controller (authoritative brain / state store)
- rt-display (UI renderer)
- rt-radio (radio appliance)
- rt-wpsd (external appliance via APIs)

Presence is derived from:
- explicit heartbeats (MQTT presence topic)
- last-seen timestamps written to Redis by the controller ingestor
- optional health fields published by nodes

Presence must never rely on:
- UI polling alone
- “last successful API request”
- implicit connectivity assumptions
---
## 2. Terms ##

### 2.1 Node Status (Authoritative) ###
Each node has a computed status:
- online: heartbeat is current
- stale: heartbeat is late but within grace window
- offline: heartbeat is beyond grace window

These status values are derived centrally by `rt-controller` and written to Redis.

### 2.2 Age ###

`age_sec` = seconds since last heartbeat was received and processed by the controller.

Age is derived from:
- last_seen_ms (epoch milliseconds)
- controller wall-clock time (authoritative time source)
---
## 3. Timing Constants (Default) ##
Defaults are intentionally conservative to avoid flapping.
- PRESENCE_TTL_SEC: 10
- PRESENCE_SWEEP_SEC: 2
- STALE_AFTER_SEC: 12
- OFFLINE_AFTER_SEC: 30
- RECOVERY_STABLE_SEC: 6 (must remain online this long before clearing “offline” condition)

***Rationale***
- TTL is “expected heartbeat cadence”
- stale means “probably alive, but not trustworthy”
- offline means “stop assuming it exists”
- recovery stability prevents rapid flip-flop from transient Wi-Fi / switch hiccups

These constants may be overridden by config, but must remain explicit and documented.
---
## 4. Redis State Contract (Authoritative) ##
Each node must have a Redis hash:
```
rt:nodes:<node_id>
```
***Required fields***
- id (string): node id
- role (string): controller/display/radio/wpsd
- status (string): online/stale/offline
- age_sec (int): seconds since last heartbeat
- last_seen_ms (int): epoch ms of last heartbeat
- last_seen_ts (string): ISO8601 UTC timestamp
- hostname (string)
- ip (string)
***Optional fields (recommended)***
- ui_render_ok (bool-ish string): “true/false”
- publisher_error (string): empty if none
- version (string): software tag or git rev
- boot_id (string): changes on reboot (used for detecting restarts)
***Authoritative Writer***
Only rt-controller presence ingestor writes computed fields:
- status
- age_sec
Nodes may publish:
- ui_render_ok
- version
- other telemetry fields
Nodes must never self-declare `status`.
---
## 5. Presence-Derived UI Behavior (Authoritative) ##
The UI must remain useful under partial failure.

### 5.1 Display node missing (rt-display offline) ###
- No in-vehicle UI available
- Controller continues normal operation
- Alerts still emitted via MQTT/Meshtastic as configured

### 5.2 Controller missing (rt-controller offline) ###
- UI must show “controller unreachable”
- No attempts to guess system state
- Display should show last-known snapshot if available, labeled as stale

### 5.3 Node stale/offline behavior in panels ###
Any panel that depends on a node/service must:
- render a ***degraded state*** when the dependency node is stale/offline
- show last-known-good data (if available) with a visible “stale” label
- avoid spinners that imply “loading forever”
- never block rendering of other panels
---
## 6. Presence-Derived Alerts (Authoritative) ##
Presence alerts must be:
- deduplicated
- rate-limited
- stateful (only emit on transitions)

### 6.1 Transition alerts ###
Emit alerts on status transitions:
- online → stale (optional, default off)
- stale → offline (default on)
- offline → online (default on, but only after RECOVERY_STABLE_SEC)

### 6.2 Rate limiting ###
For any given node:
- do not emit the same alert class more than once every 5 minutes

### 6.3 Alert outputs ###
Presence alerts may be emitted to:
- Redis alerts list/state
- MQTT alerts topic
- Meshtastic alert channel (if enabled)
Presence alerts must never:
- spam every sweep cycle
- block system operation
- trigger automatic “repair loops” unless explicitly configured
---
## 7. Presence-Derived Behaviors (Non-UI) ##
Presence may drive optional behaviors, but only if explicit:

### 7.1 Degraded mode flags ###
Controller may set a global key:
- `rt:system:degraded = true/false`
- `rt:system:degraded_reason = <short string>`
This key is derived from:
- any required node being offline
- critical service faults

### 7.2 Automatic restarts (explicit only) ###
Automatic “try to restart the missing thing” behaviors are allowed only if:
- allow-listed
- rate-limited
- logged with cause + outcome
Default: off.
---
## 8. Configuration Over Code (Requirement) ##
Presence thresholds and alert toggles must be configurable via:
- committed config files (preferred)
- systemd Environment overrides (allowed for node-local tuning)

Hardcoding thresholds in Python is prohibited unless mirrored in config and documented.
---
## 9. Required Tests / Validation ##
Presence implementation must support:
- simulated missed heartbeat → stale → offline
- recovery with stability window (no flapping)
- Redis contract fields present
- UI snapshot endpoint reflects derived status
---
***End of Presence Policy (Authoritative)***