# RT-SPEC-PANEL-INPUT-BRIDGE.md
## RollingThunder v0.46 — Panel Input Bridge Service
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative wired panel bridge service for RollingThunder.

The bridge is the controller-side ingress point for physical panel events.
It receives raw panel data from a serial-connected panel, validates it,
normalizes it, tracks panel health, and publishes accepted events into the
existing controller-owned input pipeline.

This document strictly follows:

- INTENTS.md
- RT-SPEC-CONTROLLER-INPUT.md
- RT-SPEC-PHYSICAL-CONTROL-PANEL.md
- RT-SPEC-CONTROL-MAPPING.md
- RT-SPEC-LED-STATE-MODEL.md

This document does **not** define:

- panel firmware implementation
- electrical wiring
- enclosure construction
- UI rendering
- wireless transport behavior

This document defines:

- the wired bridge service role
- raw serial framing
- raw event schema at ingress
- validation rules
- debounce responsibility
- heartbeat and health rules
- failure handling
- Redis ownership for panel health/observability
- publish contract into the controller pipeline

---

# 2. Core Principles

- Controller is the sole authority.
- Panel is a dumb event emitter.
- Redis is the source of truth for state.
- The bridge adds transport handling, not semantics.
- The bridge must fail closed.
- Same raw event + same controller state must produce the same downstream result.
- No panel event may bypass the controller pipeline.
- Debounce is handled controller-side.
- Health and freshness must be observable.

---

# 3. Scope and Placement

## 3.1 Runtime Placement

The bridge runs on **rt-controller** as a long-running service.

Suggested service name:

- `panel_input_bridge.py`

Suggested unit name:

- `rt-panel-input-bridge.service`

## 3.2 Transport

Panel v1 uses a wired serial connection over USB.

The bridge reads from:

- configured serial path, or
- auto-detected `/dev/ttyACM*` compatible device

Transport is an ingress detail only.

The raw event schema and downstream behavior MUST remain transport-independent.

---

# 4. Service Responsibilities

The bridge MUST perform all of the following:

1. open and monitor the serial transport
2. receive framed raw events
3. validate message framing
4. validate schema version
5. validate required fields
6. validate panel identity
7. validate sequence behavior
8. perform controller-side debounce
9. normalize accepted raw events
10. publish accepted raw events into the existing controller input path
11. publish rejection observability
12. publish panel heartbeat/health state
13. publish transport status
14. fail safely when state is missing or malformed

The bridge MUST NOT:

- interpret page context
- decide final intents
- execute actions
- mutate UI state directly
- emulate missing controller state
- cache semantic context
- implement hardware-side business logic in software

---

# 5. End-to-End Position in the Pipeline

The bridge sits at the ingress side of the existing controller pipeline.

Authoritative flow:

`serial_frame_received → frame_validation → raw_event_parse → schema_validation → panel_validation → sequence_validation → debounce_filter → normalization → publish_to_controller_input_pipeline`

After publication, the existing controller pipeline continues unchanged:

`physical_mapping → semantic_mapping → intent_validation → execution → state_update → bus_publish → observability_log`

The bridge MUST NOT collapse or skip downstream stages.

---

# 6. Raw Serial Framing

## 6.1 Framing Requirement

Each event MUST be sent as one complete framed message.

Panel v1 framing format:

- one JSON object per line
- UTF-8
- newline-delimited

This provides simple deterministic parsing and easy observability.

## 6.2 Frame Rules

- one line = one event
- blank lines ignored
- oversized frames rejected
- non-UTF-8 rejected
- malformed JSON rejected
- partial frames buffered only until newline or timeout
- timeout while waiting for line completion causes frame discard

## 6.3 Frame Size Limit

A maximum frame size MUST be enforced.

Recommended rule:

- reject any frame over 512 bytes

This keeps ingress bounded and protects the bridge from malformed or runaway emitters.

---

# 7. Raw Event Schema at Ingress

## 7.1 Required Schema

All accepted raw panel events MUST normalize to this schema before entering the controller pipeline:

```json
{
  "schema": 1,
  "event_id": "<string>",
  "panel_id": "<string>",
  "control_id": "<string>",
  "event_type": "<string>",
  "value": <number|null>,
  "panel_ts_ms": <integer|null>,
  "seq": <integer>
}
```

## 7.2 Required Fields

- `schema`
- `event_id`
- `panel_id`
- `control_id`
- `event_type`
- `seq`

## 7.3 Optional Fields

- `value`
- `panel_ts_ms`

## 7.4 Event Type Rules

Allowed event types for panel v1:

Buttons:
- `press`
- `hold`
- `repeat` only if later enabled by controller policy

Encoder:
- `rotate`
- `press`
- `hold`

## 7.5 Value Rules

- `value` is required for `rotate`
- `value` MUST be integer `+1` or `-1` for panel v1
- `value` MUST be null or absent for button press/hold events

## 7.6 Unknown Fields

Unknown fields may be ignored for forward compatibility, but they MUST NOT change behavior.

---

# 8. Panel Identity and Registration

## 8.1 Panel ID

Each panel MUST emit a stable `panel_id`.

Example:

- `panel-v1-main`

## 8.2 Known Panel Validation

The bridge MUST validate that the panel is allowed.

Allowed panel identity may be configured by:

- exact `panel_id`, or
- approved panel family/version list

Unknown or disallowed panel IDs MUST be rejected.

## 8.3 Single Active Panel Rule for v1

Panel v1 assumes one active wired primary panel.

If multiple devices are present, only the configured/selected panel may be treated as active input unless a later multi-panel spec defines arbitration.

---

# 9. Sequence Validation

Sequence validation is mandatory.

## 9.1 Rules

For each active `panel_id`:

- duplicate `seq` → drop
- backward `seq` → drop
- forward gaps → allowed
- reset after reconnect allowed only through disconnect/re-register handling

## 9.2 No Blocking on Gaps

The bridge MUST NOT stall waiting for missing sequence numbers.

## 9.3 Reconnect Behavior

When a disconnect/reconnect is detected, the bridge MUST:

1. mark prior panel session stale/offline
2. create a new transport session
3. allow sequence to restart for the newly recognized session

Sequence reset without a reconnect boundary MUST be treated as invalid.

---

# 10. Debounce Model

## 10.1 Responsibility

Debounce is handled by the bridge on the controller.

The panel firmware should emit simple edge-derived events only.

## 10.2 Debounce Rule

Debounce is applied only to repeated equivalent raw button events that occur inside a bounded suppression window.

Equivalent means same:

- `panel_id`
- `control_id`
- `event_type`
- same effective value

## 10.3 Debounce Scope

Debounce applies to:

- button `press`
- button `hold` if noisy duplicates are emitted
- encoder push button `press`/`hold`

Debounce does **not** merge legitimate navigation rotation steps.

## 10.4 Encoder Rule

Encoder `rotate` events are not classic contact bounce events for controller semantics.
They MUST be preserved as independent steps unless exact duplicate transport artifacts are detected.

## 10.5 Determinism Requirement

Debounce behavior MUST be deterministic and bounded.

It must never infer intent or create synthetic actions.

---

# 11. Heartbeat and Panel Presence

## 11.1 Heartbeat Requirement

The panel SHOULD emit heartbeat frames at a fixed interval.

Suggested heartbeat message:

```json
{
  "schema": 1,
  "type": "heartbeat",
  "panel_id": "panel-v1-main",
  "seq": 1234,
  "panel_ts_ms": 4567890
}
```

## 11.2 Bridge Handling

The bridge MUST track:

- last frame received time
- last valid event time
- last heartbeat time
- transport connected/disconnected state

## 11.3 Health Freshness States

The bridge MUST derive panel health freshness from observed traffic.

Canonical health states:

- `online`
- `stale`
- `offline`
- `invalid`

Definitions:

- `online`: valid frames/heartbeats received within freshness window
- `stale`: transport exists but heartbeat/event freshness window exceeded
- `offline`: serial device absent or disconnected
- `invalid`: device connected but frames consistently malformed or rejected

---

# 12. Redis Ownership and Published State

The bridge may publish bridge-owned transport and health keys.
It MUST NOT publish semantic UI state.

## 12.1 Required Health Key

Per prior controller conventions:

- `rt:panel:<panel_id>:health`

Recommended fields:

- `panel_id`
- `status` (`online|stale|offline|invalid`)
- `transport` (`serial`)
- `device_path`
- `last_seen_ms`
- `last_valid_event_ms`
- `last_heartbeat_ms`
- `last_error`
- `schema`
- `session_id`
- `seq_last`

## 12.2 Optional Bridge Status Key

Optional additional bridge key:

- `rt:panel:<panel_id>:bridge`

Recommended fields:

- `bridge_service`
- `bridge_version`
- `transport_connected`
- `device_path`
- `reconnect_count`
- `rejection_count`
- `accepted_count`
- `last_start_ms`

## 12.3 Input Observability Keys

The bridge should continue compatibility with controller-owned observability such as:

- `rt:input:last_accepted`
- `rt:input:last_rejected`

If the existing controller pipeline already owns writing these, the bridge should publish into the ingestion channel and let the controller pipeline remain authoritative.

---

# 13. Publish Contract into Controller Input Pipeline

## 13.1 Bridge Output

For accepted events, the bridge publishes normalized raw events into the existing controller input path.

The bridge output MUST still be a **raw normalized input event**, not a resolved intent.

## 13.2 Normalized Event Shape

The bridge MUST attach controller ingress metadata before publishing downstream.

Required normalized output:

```json
{
  "schema": 1,
  "event_id": "<string>",
  "panel_id": "<string>",
  "control_id": "<string>",
  "event_type": "<string>",
  "value": <number|null>,
  "timestamp": "<iso8601>",
  "seq": <integer>,
  "source": "panel.serial",
  "device_path": "<string>",
  "session_id": "<string>"
}
```

## 13.3 Downstream Ownership

After this publish point, the existing v0.42/v0.44 machinery remains authoritative for:

- physical mapping
- semantic mapping
- intent validation
- execution
- state mutation
- bus publication

---

# 14. Error and Rejection Categories

The bridge MUST classify failures clearly.

## 14.1 Reject Categories

- frame_too_large
- utf8_invalid
- json_invalid
- schema_missing
- schema_unsupported
- panel_unknown
- control_unknown
- event_type_invalid
- value_invalid
- seq_duplicate
- seq_backward
- debounce_suppressed
- transport_timeout
- reconnect_in_progress

## 14.2 Reject Handling

For rejected events, the bridge MUST:

1. not execute anything
2. not mutate semantic state
3. record observability
4. preserve service operation when safe

Malformed traffic from the panel must not crash the bridge.

---

# 15. Failure Handling

## 15.1 Serial Device Missing at Startup

Behavior:

- service stays running
- health publishes `offline`
- bridge retries connection
- no synthetic events emitted

## 15.2 Device Removed While Running

Behavior:

- mark panel `offline`
- close dead handle
- start reconnect loop
- sequence session resets only after reconnect

## 15.3 Malformed Event Burst

Behavior:

- reject malformed frames individually
- increment rejection counters
- if persistent, panel health may degrade to `invalid`
- do not forward malformed events

## 15.4 Missing Controller State Elsewhere

The bridge does not depend on page/modal state for ingress.
It may continue receiving and forwarding normalized raw events even if other application state is degraded.

If downstream controller components reject later, that rejection remains downstream responsibility.

## 15.5 Bridge Internal Exception

Behavior:

- catch and log exception
- preserve process when safe
- if unrecoverable, exit non-zero so systemd may restart cleanly

---

# 16. Reconnect Model

## 16.1 Auto-Reconnect

The bridge SHOULD continuously attempt reconnect while the service is enabled.

## 16.2 New Session Boundary

Every successful reconnect MUST create a new `session_id`.

This prevents ambiguity around sequence reuse and stale transport assumptions.

## 16.3 No Replay

The bridge MUST NOT replay previously seen events after reconnect.

---

# 17. Configuration

Recommended environment/config options:

- serial device path or discovery pattern
- baud rate if needed by firmware
- allowed panel IDs
- frame size limit
- debounce enable/disable
- debounce window
- heartbeat stale threshold
- offline threshold
- reconnect retry interval
- logging verbosity

Configuration must be explicit and deterministic.
No hidden auto-magic beyond bounded serial device discovery.

---

# 18. Logging and Observability

The bridge MUST log enough to support field diagnosis.

Required observability coverage:

- serial open success/failure
- panel registration
- reconnect events
- accepted event count
- rejected event count
- rejection reason
- health transitions
- device path changes
- session boundaries

Sensitive or excessive log spam should be avoided.
Repeated identical errors should be rate-limited if needed, without hiding state transitions.

---

# 19. Safety Rules

- The bridge never resolves destructive actions.
- The bridge never assumes success.
- The bridge never emits semantic state changes directly.
- Rejected or malformed raw events produce no control action.
- Loss of panel connectivity must fail passive, not active.
- Reconnect must not create phantom button actions.
- Debounce must suppress noise, not user intent.

---

# 20. Acceptance Criteria

The bridge is complete when all of the following are true:

1. service starts cleanly on rt-controller
2. serial panel can be detected and connected
3. malformed frames are rejected safely
4. duplicate/backward sequence events are dropped
5. bounce does not produce duplicate actions
6. encoder rotation remains responsive and step-accurate
7. valid events are normalized and forwarded into the existing controller path
8. panel health is published in Redis
9. unplug/replug recovers without duplicate or phantom events
10. bridge failure is observable and restart-safe
11. no event bypasses the controller pipeline
12. controller remains sole authority for state and intent execution

---

# 21. Explicit Non-Goals for v0.46

This version does not define:

- wireless transport
- multiple simultaneous active control panels
- combo button chords
- macro actions
- firmware-side LED logic
- direct intent emission from panel firmware
- alternate execution paths

These may be specified later only if they preserve the same raw event model and controller authority.

---

# 22. Recommended Next Stage

The next stage after this bridge spec should be:

**controller-side page transition execution and lifecycle verification**

That stage should verify:

- `ui.page.next`
- `ui.page.prev`
- page order handling
- focus reset on page change
- browse reset on page change
- service stop/start ordering
- allowedIntents enforcement across pages

---

# Final Rule

The panel bridge is a transport ingress service only.
It must convert wired serial panel traffic into validated, normalized controller input events and nothing more.
All meaning, intent resolution, safety enforcement, execution, and state ownership remain with the RollingThunder controller.
