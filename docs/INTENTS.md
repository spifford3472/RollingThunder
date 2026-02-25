# RollingThunder Intent Vocabulary #

## Authoritative Reference ##

This document defines the **canonical intent vocabulary** used throughout the
RollingThunder platform.

An *intent* represents **what the user or system wants to happen**, not how it is
implemented or which component performs it.

All control paths—physical buttons, UI widgets, Meshtastic commands, automation,
and future integrations—emit intents.

If behavior cannot be described as an intent, it does not belong in this system.

## 1. Why Intents Exist ##

RollingThunder deliberately separates:
- **Input sources** (buttons, UI, Meshtastic, scripts)
- **Intent** (requested action)
- **Execution** (controller logic, service logic)

This ensures:
- deterministic behavior
- uniform safety enforcement
- auditable control flow
- no hidden bypass paths
There is exactly **one control vocabulary.**

## 2. Intent Structure ##

An intent is a small structured object:
`
{
  "intent": "ui.page.next",
  "params": { }
}
`
## Required fields ##
- intent — canonical string identifier

## Optional fields ##
- params — small, bounded JSON object
- source — injected by runtime (not user-defined)
- timestamp — injected by runtime (not user-defined)
Intents must be:
- small
- declarative
- side-effect free until executed by the controller

## 3. Intent Naming Conventions ##

Intent names use **dot-separated namespaces:**
`
<domain>.<subdomain>.<action>
`

### Rules ###
- lowercase only
- verbs last
- stable over time
- never overloaded
Good:
- ui.page.next
- radio.hf.query
- alert.ack
Bad:
- changePage
- doStuff
- hfStatus

## 4. Intent Domains ##
### 4.1 UI Navigation (ui.*) ###

Used for page and panel navigation only.

|Intent | Purpose|
| --------------- | -------------------------- |
| ui.page.next	| Move to next page (by order) |
| ui.page.prev	| Move to previous page |
| ui.page.goto	| Go to specific page ID |
| ui.focus.next	| Advance focus to next panel |
| ui.focus.prev	| Move focus to previous panel |
| ui.ok	| Confirm current selection |
| ui.cancel	| Cancel current action |
| ui.open	| Open a logical UI target |

Example:

```
{
    { "intent": "ui.page.goto", "params": { "pageId": "hf" } }
}
```

## 4.2 Alert Control (alert.*) ##

Used to acknowledge, silence, or manage alerts.

| Intent | Purpose |
| --------------- | ---------------------------- |
| alert.ack | Acknowledge alert(s) |
| alert.silence | Temporarily silence alerts |
| alert.clear | Clear non-persistent alerts |

Example:
```
{
{ "intent": "alert.ack", "params": { "scope": "focused" } }
}
```

## 4.3 Host & System (host.*) ##

Read-only system introspection unless explicitly enabled.

| Intent | Purpose|
| ------------- | ----------------------- |
| host.status | Request system status |
| host.uptime | Request uptime |
| host.version | Request version info |

Example:
```
{
{ "intent": "host.status", "params": { "scope": "summary" } }
}
```

## 4.4 Service Control (service.*) ##

Service lifecycle control.
**Disabled by default for external control paths.**

| Intent | Purpose |
| ----------------- | -------------------- |
| service.start | Start a service |
| service.stop | Stop a service |
| service.restart | Restart a service |
| service.status | Query service state |

Example:
```
{
{ "intent": "service.restart", "params": { "serviceId": "gps_ingest" } }
}
```
### Service Control Parameters (Authoritative) ###

All `service.*` intents MUST use the following parameter schema.

#### Required params ####
- `serviceId` (string): Logical RollingThunder service ID from the config service catalog
  - Example: `gps_ingest`, `mqtt_bus`, `noaa_same`
  - MUST NOT be a systemd unit name
  - MUST be allow-listed by the controller based on `services.*` config
  - MUST be owned by the executing node (e.g. `ownerNode: rt-controller`) unless explicitly enabled otherwise

#### Forbidden params ####
- `unit`, `systemdUnit`, `command`, `args`, or any OS-level identifier
  - The UI must never request a unit name or a shell command.
  - Mapping `serviceId -> systemd unit` is controller-owned configuration.

## 4.5 Radio Control (radio.*) ##

Radio intents are **read-only by default.**

**HF radio**
| Intent | Purpose |
| ---------------- | ------------------- |
| radio.hf.query | Query HF snapshot|
| radio.hf.status | Request HF status|

Future write-capable intents (explicitly gated):
- radio.hf.set.freq
- radio.hf.set.mode
- radio.hf.ptt

These must be:
 - allow-listed
 - safety-checked
 - auditable

## 4.6 Page Lifecycle (page.*) ##

Internal lifecycle hints (rarely emitted by inputs).

| Intent | Purpose |
| ------------ | --------------------- |
| page.enter | Page became active |
| page.exit | Page exited |

These intents are primarily for logging and service coordination.

## 5. Safety & Constraint Enforcement ##

Intents are **filtered and validated** before execution.

Constraints may include:
 - driving mode
 - page context
 - focus context
 - sender allow-list (Meshtastic)
 - rate limits

If an intent violates constraints:
 - it is rejected
 - rejection is logged
 - optional status is returned to sender

No intent may bypass safety rules.

## 6. External Control Paths (Meshtastic) ##

Meshtastic messages are translated into intents.

Rules:
 - Meshtastic never executes logic directly
 - Meshtastic may only emit allow-listed intents
 - Meshtastic intents flow through the same validation path
 - Meshtastic is never required for normal operation

This prevents a secondary control plane from emerging.

## 7. Forward Compatibility Rules ##

- New intents may be added freely
- Existing intents must not change meaning
- Deprecated intents must be documented, not reused
- Unknown intents must be ignored safely

## 8. Non-Negotiable Invariants ##
1. All control paths emit intents
2. Intents are declarative, not imperative
3. Intents never encode implementation details
4. Safety rules apply uniformly
5. Intent strings are stable identifiers

If behavior cannot be described as an intent, the architecture must be revisited.

## 9. How This Document Is Used ##

Consult this document when:
- adding buttons or controls
- adding Meshtastic commands
- adding UI actions
- adding automation
- reviewing safety behavior

If two components invent different words for the same action, the system is already drifting.