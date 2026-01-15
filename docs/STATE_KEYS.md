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
`rt:node:<nodeId>`

**Type:** object
**Meaning:** per-node detailed health snapshot

Examples:
- `rt:node:rt-controller`
- `rt:node:rt-radio`

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
- `rt:node:rt-radio` (heartbeat)

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