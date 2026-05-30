# RollingThunder State Keys  
**Authoritative Reference**

This document defines the **canonical state key namespace** used by RollingThunder.

State lives in Redis and is the **authoritative source of truth** for:
- current system state
- node health
- GPS and time
- radio snapshots
- alerts
- service readiness

Panels read state keys. Services write state keys.  
No other mechanism is allowed to become a parallel state store.

---

## 1. Core Principles

1. **Redis is authoritative**  
   If it’s “current truth,” it must be reflected in Redis.

2. **Keys are stable identifiers**  
   Key names must not change meaning.

3. **Write paths are controlled**  
   Only the owning node/service writes a given key namespace.

4. **State is structured and bounded**  
   Values are small JSON objects or small primitives.  
   Large payloads belong in logs or files, referenced by pointer if needed.

5. **Freshness is explicit**  
   Any state that can go stale must carry timestamps.

---

## 2. Namespace Format

All keys are prefixed by the configured namespace:

- `globals.state.namespace` (default: `rt`)

### Canonical key pattern
```
{
<ns>:<domain>[:<subdomain>[:<name>]]

}
```

### Examples

- `rt:gps:fix`
- `rt:alerts:active`
- `rt:hf:snapshot`
- `rt:nodes:health`

### Rules

- lowercase only  
- `:` separators only (no dots in keys)  
- domains are stable and limited  
- keys must be human-readable  

---

## 3. Value Conventions

### 3.1 Common JSON Fields (Recommended)

Most JSON values should include:

- `ts` — ISO-8601 timestamp or unix epoch ms  
- `source` — service ID or node ID  
- `staleAfterMs` — optional hint for consumers  
- `ok` — optional quick health indicator  

Example snapshot:

```json
{
  "ts": "2026-01-14T21:10:05Z",
  "source": "gps_ingest",
  "ok": true,
  "data": {
    "example": "value"
  }
}
```

### 3.2 Primitives vs Objects ###

Allowed:
- small primitives (true, 42, "hf")
- small JSON objects
Avoid:
- large arrays
- binary blobs
- multi-kilobyte free-text payloads

## 4. Domains and Canonical Keys ##
### 4.1 System / UI State ####

`rt:system:page`
**Type:** string
**Meaning:** active page ID

Example:
```json
"hf"
```
---
`rt:system:focus`

**Type:** object
**Meaning:** focused panel ID

Example:
```json
{
  "panelId": "hf_status",
  "ts": "2026-01-14T21:10:05Z"
}
```
---
`rt:system:driving`

**Type:** object
**Meaning:** driving-mode evaluation

Example:
```json
{
  "active": true,
  "mph": 32.1,
  "ts": "2026-01-14T21:10:05Z"
}
```

### 4.2 GPS / Time ###
`rt:gps:fix`

**Type:** object
**Meaning:** authoritative GPS position

Suggested fields:
- lat
- lon
- alt_m
- hdop (optional)
- fixType (optional)
- ts
---
`rt:gps:speed`

**Type:** object or number
**Meaning:** current speed used for driving-mode logic
---
`rt:gps:time`

**Type:** string
**Meaning:** authoritative time derived from GPS
---
### 4.3 Nodes / Health ###
`rt:nodes:health`

**Type:** object
**Meaning:** roll-up health summary for all nodes

Example:
```json
{
  "ts": "2026-01-14T21:10:05Z",
  "nodes": {
    "rt-controller": { "ok": true, "lastSeenTs": "...", "summary": "ok" },
    "rt-display":    { "ok": true, "lastSeenTs": "...", "summary": "ok" },
    "rt-radio":      { "ok": false, "lastSeenTs": "...", "summary": "unreachable" },
    "rt-wpsd":       { "ok": true, "lastSeenTs": "...", "summary": "ok" }
  }
}
```
---
`rt:nodes:<nodeId>`

**Type:** object
**Meaning:** per-node detailed health snapshot

Examples:
- `rt:nodes:rt-controller`
- `rt:nodes:rt-radio`

### 4.4 Services ###
`rt:services:state`

**Type:** object
**Meaning:** controller view of all service states

Example:
```json
{
  "ts": "2026-01-14T21:10:05Z",
  "services": {
    "gps_ingest": { "running": true, "ok": true, "sinceTs": "..." },
    "noaa_same":  { "running": true, "ok": true, "sinceTs": "..." }
  }
}
```
---
`rt:service:<serviceId>`

**Type:** object
**Meaning:** detailed per-service status
---
### 4.5 Alerts (Normalized) ###
`rt:alerts:active`

**Type:** array (bounded)
**Meaning:** list of active alerts

Rules:
- bounded size (e.g., max 20)
- deduplicated
- ordered by severity and time
---
`rt:alerts:focused`

**Type:** object or string
**Meaning:** UI focus pointer into active alerts
---
`rt:alerts:history`

**Type:** array (bounded)
**Meaning:** recent cleared or acknowledged alerts
---
### 4.6 NOAA ###
`rt:noaa:decoder`

**Type:** object
**Meaning:** NOAA decoder heartbeat / readiness
---
`rt:noaa:station`

**Type:** object
**Meaning:** active NOAA station metadata
---
`rt:noaa:county`

**Type:** object
**Meaning:** derived county and SAME targeting context
---
### 4.7 Radio ###
**HF Snapshot**
`rt:hf:snapshot`
**Type:** object
**Meaning:** HF radio status (read-only by default)

Suggested fields:
- `freq_hz`
- `mode`
- `ptt`
- `tx`
- `power_w` (optional)
- `ts`
---
### 4.7.1 VHF Radio / Scan ###

`rt:vhf:adapter`

**Owner:** `rt-radio` / `vhf_ic2730a_adapter_status`

**Type:** object

**Meaning:** Low-level IC-2730A adapter/control-path status. This is not a UI command surface.

`rt:vhf:radio`

**Owner:** `rt-controller` / `vhf_radio_monitor`

**Type:** object

**Meaning:** Controller-facing VHF radio availability model derived from `rt:vhf:adapter`.

Important controller command rule:

The VHF radio command path is available only when:

```json
{
  "available": true,
  "status": "available"
}

or another explicitly documented ready status such as "ready" is present.

Recommended fields:

{
  "available": true,
  "status": "available",
  "command_available": true,
  "command_ready_statuses": ["available", "ready"],
  "radio_name": "Icom IC-2730A",
  "adapter_name": "ic2730a",
  "port": "/dev/ic2730a",
  "reason": "IC-2730A radio/control path available.",
  "adapter_status": "detected",
  "adapter_control_mode": "hamlib_write_test",
  "source": "vhf_radio_monitor",
  "updated_utc": "2026-05-30T00:00:00Z"
}

rt:vhf:scan:request

Owner: intent worker/controller input path

Type: object

Meaning: User-requested VHF scan enable/disable state. This key is an intent-derived request only. It must not command the radio directly.

rt:vhf:scan

Owner: rt-controller / vhf_repeater_scan_manager

Type: object

Meaning: Controller-owned VHF repeater software scan state.

Recommended fields:

{
  "enabled": true,
  "requested": true,
  "mode": "repeaters",
  "scanning": true,
  "actual_scan_state": "scanning",
  "status": "scanning",
  "reason": "Scanning repeater example.",
  "current_index": 0,
  "current_frequency_mhz": 146.94,
  "current_repeater_id": "example",
  "current_repeater": {},
  "last_squelch_activity_utc": null,
  "last_ptt_activity_utc": null,
  "last_user_frequency_change_utc": null,
  "dwell_ms": 500,
  "confirm_squelch_seconds": 5,
  "resume_idle_seconds": 15,
  "repeater_radius_miles": 25,
  "map_radius_miles": 30,
  "gps_reload_distance_miles": 5,
  "ptt_reload_holdoff_seconds": 180,
  "squelch_reload_holdoff_seconds": 120,
  "repeater_count": 12,
  "nearby_count": 12,
  "source": "vhf_repeater_scan_manager",
  "updated_utc": "2026-05-30T00:00:00Z"
}

Safety rules:

UI controls emit intents only.
UI must not command VHF radio actions.
UI must not calculate scan targets, distance, C/D banks, or SQLite repeater lookups.
Controller must check rt:vhf:radio availability before adapter requests that tune, scan, read squelch, read RX/TX status, read S-meter, write memory, select memory, or otherwise touch the radio.
Adapter remains the only place that knows IC-2730A, CI-V, serial, or Hamlib details.

---

### 5. `docs/INTENTS.md`

In the `vhf.scan.set_enabled` section, replace the current incomplete code block area with this cleaned text:

```markdown
### 4.82 VHF scan intent

#### `vhf.scan.set_enabled`

Purpose:

Requests that the controller enable or disable the VHF repeater scan state machine.

This is an intent-derived request only. It does not command the radio directly.

Payload examples:

```json
{
  "intent": "vhf.scan.set_enabled",
  "enabled": true
}

{
  "type": "vhf.scan.set_enabled",
  "payload": {
    "enabled": false
  }
}

Rules:

UI/browser controls may emit this intent.
The intent worker may write rt:vhf:scan:request.
The controller owns rt:vhf:scan.
The controller must check rt:vhf:radio before radio-touching adapter requests.
The adapter owns all IC-2730A / CI-V / serial behavior.
This intent must not write memories, clear memories, load C/D banks, start built-in radio scan, program Side B, or expose PTT/transmit controls.

The current docs already define `vhf.scan.set_enabled`, but the markdown fence is incomplete around the payload examples. :contentReference[oaicite:9]{index=9}

## Verification commands

### Syntax checks

```bash
cd /opt/rollingthunder

python3 -m py_compile \
  nodes/rt-controller/services/vhf_repeater_scan_manager.py \
  nodes/rt-controller/services/vhf_radio_monitor.py \
  nodes/rt-radio/services/vhf_ic2730a_adapter_status.py \
  nodes/rt-radio/services/ic2730a_adapter.py \
  tools/ui_intent_worker.py

python3 -m json.tool config/app.json >/dev/null


---
### 4.8 Meshtastic ###
'rt:meshtastic:link'

**Type:** object
**Meaning:** Meshtastic link heartbeat

Suggested fields:
- `ok`
- `nodeId`
- `lastRxTs`
- `lastTxTs`
- `ts`
---
`rt:meshtastic:last_cmd`

**Type:** object
**Meaning:** last received Meshtastic command (bounded, audit-only)
---

### 5. Ownership Rules (Who Writes What) ###

To prevent hidden coupling:

**Controller (**'rt-controller'**) writes:**
- `rt:system:*`
- `rt:gps:*`
- `rt:alerts:*`
- `rt:nodes:*`
- `rt:services:*`
- `rt:noaa:*`
- `rt:meshtastic:*`

**Radio appliance (**`rt-radio`**) writes:**
- `rt:hf:*`
- `rt:nodes:rt-radio` (heartbeat)

**Display (**`rt-display`**) writes:**
- **nothing**

**External systems (**`rt-wpsd`**):**
- treated as read-only integrations
- controller may cache under `rt:dmr:*` if needed

Any violation of ownership is architectural drift.
---
## 6. Freshness and Staleness ##

State is considered stale if:
- `now - ts > staleAfterMs`
- or if policy declares it stale

Panels must gracefully handle:
- missing keys
- stale values
- partial data

No UI element may crash due to missing state.
---
## 7. Evolution and Compatibility ##
- New keys may be added freely
- Existing keys must not change meaning
- If semantics change, create a new key
- Consumers must ignore unknown fields
---
### 8. Non-Negotiable Invariants ###
1. Redis is the authoritative state store
2. Keys have stable meaning
3. Only the owning node/service writes a key
4. State is bounded and structured
5. Staleness is explicit and handled gracefully