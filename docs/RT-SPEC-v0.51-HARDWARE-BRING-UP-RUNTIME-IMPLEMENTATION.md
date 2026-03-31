# RT-SPEC-v0.51 — Hardware Bring-Up & Runtime Implementation
## Status: Implementation Blueprint

## 1. Purpose

This stage defines the first buildable RollingThunder hardware/runtime implementation for the wired control panel on `rt-controller`.

It does **not** change architecture. It implements the already-authoritative rules from:

- INTENTS.md
- v0.42 Controller Input Pipeline
- v0.43 Physical Control Panel
- v0.44 Control Mapping
- v0.45 LED State Model
- v0.46 Panel Input Bridge
- v0.47 Page Transitions
- v0.48 Interaction State
- v0.49 Intent Execution
- v0.50 Execution Engine

The design goal is simple:

**You can wire it, boot it, press buttons, turn the encoder, see LED truth, and observe deterministic controller behavior today.**

---

## 2. Build Scope for v0.51

v0.51 includes:

- one wired USB serial control panel
- one ESP32-based panel firmware acting only as event emitter + LED sink
- controller-side panel bridge service
- controller-side input pipeline service
- controller-side execution engine service
- controller-side LED derivation service
- controller-side LED transport publisher
- controller-side LED serial output driver
- systemd units and deployment layout
- hardware wiring assumptions
- deterministic bring-up and test procedure

v0.51 explicitly excludes:

- Bluetooth panel transport
- panel-side semantics
- alternate control paths
- local LED behavior in firmware beyond safe boot fallback
- speculative new intents or schema redesign

---

## 3. Authoritative Runtime Topology

### 3.1 Required Services on `rt-controller`

Implement the first working runtime as six always-on services:

1. `rt-panel-input-bridge.service`
   - reads panel USB serial input
   - validates framing, identity, sequence, debounce
   - publishes normalized raw events

2. `rt-input-pipeline.service`
   - consumes normalized raw events
   - runs physical mapping
   - resolves interaction state
   - maps to validated intents
   - forwards admitted validated intents to execution intake

3. `rt-execution-engine.service`
   - owns intake slot / wait slot
   - owns execution lock
   - executes v0.49 semantics
   - persists pending async records
   - reconciles async completion results

4. `rt-led-deriver.service`
   - watches authoritative state and result state
   - computes canonical LED object
   - writes controller-owned LED projection

5. `rt-panel-led-output.service`
   - converts canonical LED state into compact panel transport frames
   - rate-limits redundant LED writes
   - retransmits full snapshot on reconnect/revision gap

6. `rt-exec-timeout-watch.service`
   - sweeps pending executions by deadline
   - terminalizes timeouts under the same execution authority

### 3.2 Recommended Process Model

Keep services separate for clarity during bring-up, but share a small common library package:

- `nodes/rt-controller/lib/rt_common/redis.py`
- `nodes/rt-controller/lib/rt_common/timeutil.py`
- `nodes/rt-controller/lib/rt_common/logging.py`
- `nodes/rt-controller/lib/rt_common/events.py`
- `nodes/rt-controller/lib/rt_common/config.py`
- `nodes/rt-controller/lib/rt_common/exec_ids.py`

This keeps field debugging simpler and preserves architectural boundaries.

---

## 4. Repository File Layout

Recommended initial file layout:

```text
nodes/rt-controller/
  services/
    panel_input_bridge.py
    input_pipeline.py
    execution_engine.py
    execution_reconciler.py
    execution_timeout_watch.py
    led_deriver.py
    panel_led_output.py
  lib/
    rt_common/
      config.py
      redis_client.py
      bus.py
      clock.py
      ids.py
      health.py
      locks.py
      jsonlog.py
    rt_input/
      schemas.py
      panel_registry.py
      debounce.py
      physical_mapping.py
      semantic_mapping.py
      interaction_resolver.py
      intent_validation.py
    rt_exec/
      intake.py
      scheduler.py
      plans.py
      atomic_mutations.py
      pending_registry.py
      dispatcher.py
      reconciler.py
      family_policy.py
      observability.py
    rt_led/
      derivation.py
      prioritization.py
      transport_frames.py
  firmware/
    rt_panel_v1/
      rt_panel_v1.ino
  systemd/
    rt-panel-input-bridge.service
    rt-input-pipeline.service
    rt-execution-engine.service
    rt-execution-timeout-watch.service
    rt-led-deriver.service
    rt-panel-led-output.service
  config/
    panel-v1.json
    execution-families.json
    led-panels.json
```

---

## 5. Hardware Decision for v0.51

### 5.1 Control MCU

Use **one USB-C ESP32 board** as the control panel MCU for v0.51.

Reason:

- native USB serial is straightforward for the wired bridge
- enough GPIO for 10 buttons, 10 LED outputs, and one encoder when multiplexing or using expanders if needed
- easy firmware iteration
- clean disconnect/reconnect behavior

### 5.2 Transport

Use **wired USB serial only**.

No Bluetooth in v0.51.
Bluetooth can come later, but the current bridge spec is wired serial and the first bring-up should match that exactly.

### 5.3 Physical Controls Used Initially

For first bring-up, wire the minimum build first:

- encoder A/B + encoder push switch
- Blue BACK button
- Blue PAGE button
- Green button
- Red button
- one Yellow button
- one White button
- LEDs for those six buttons

The remaining buttons can be wired after the runtime is proven.

This keeps bring-up fast while preserving the final mapping model.

---

## 6. Firmware Role (Strictly Dumb)

### 6.1 Firmware Responsibilities

Firmware does only four things:

1. scans physical inputs
2. detects edge/hold transitions
3. emits framed raw events
4. receives LED state frames and drives output pins

### 6.2 Firmware Must Not Do

Firmware must not:

- map controls to intents
- interpret page or modal state
- debounce semantically
- remember controller meaning
- animate LEDs based on guesses
- retry actions on behalf of the controller

### 6.3 Firmware Boot Behavior

Safe boot behavior:

- all LEDs off at power-up
- start USB serial
- emit heartbeat once per second
- begin input scanning
- accept LED snapshot from controller

If controller disappears:

- keep last LED frame only for a short stale timeout (for example 2 seconds)
- then fall back to safe degraded posture:
  - red blink fast
  - all other LEDs off

This is transport-safe fallback only, not semantic interpretation.

---

## 7. Panel Firmware Event Protocol

### 7.1 Input Frames to Controller

One JSON object per line:

```json
{"schema":1,"event_id":"01HT...","panel_id":"panel-v1-main","control_id":"enc","event_type":"rotate","value":1,"panel_ts_ms":1234567,"seq":1001}
```

```json
{"schema":1,"event_id":"01HT...","panel_id":"panel-v1-main","control_id":"btn_green_1","event_type":"press","seq":1002}
```

```json
{"schema":1,"type":"heartbeat","panel_id":"panel-v1-main","seq":1003,"panel_ts_ms":1234999}
```

### 7.2 Hold Generation

Firmware should emit `hold` once when the hold threshold is crossed.

Recommended initial thresholds:

- hold threshold: 700 ms
- no repeat events in v0.51 except optional encoder repeat later

### 7.3 Sequence Handling

- increment `seq` on every event and heartbeat
- reset only on reboot/reconnect
- never replay historical events

---

## 8. LED Output Protocol to Panel

### 8.1 Controller-Owned Canonical LED Projection

Controller writes a Redis projection such as:

- `rt:panel:panel-v1-main:led_state`

Payload:

```json
{
  "panel_id": "panel-v1-main",
  "revision": 42,
  "generated_at": "2026-03-28T18:00:00Z",
  "source_state_epoch_ms": 1774720800000,
  "leds": {
    "btn_green_1": {"mode":"on","reason":"primary_available","priority":"normal","color_semantic":"green"},
    "btn_red_1": {"mode":"off","reason":"none","priority":"normal","color_semantic":"red"},
    "btn_blue_page": {"mode":"pulse","reason":"page_nav_available","priority":"context","color_semantic":"blue"}
  }
}
```

### 8.2 Transport Frames Sent to Panel

Use a separate compact line protocol over the same USB serial link.
Each line is controller → panel JSON.

Full snapshot:

```json
{"type":"led_snapshot","panel_id":"panel-v1-main","revision":42,"leds":{"btn_green_1":"on","btn_red_1":"off","btn_blue_page":"pulse"}}
```

Incremental update:

```json
{"type":"led_delta","panel_id":"panel-v1-main","revision":43,"changes":{"btn_red_1":"blink_fast"}}
```

Panel ack:

```json
{"schema":1,"type":"led_ack","panel_id":"panel-v1-main","revision":43,"seq":1040}
```

### 8.3 One-Way Authority Rule

The panel may acknowledge revision receipt, but the acknowledgment has **no semantic meaning**.
LED truth is still controller-owned.

---

## 9. Control IDs for First Build

Use stable IDs from day one:

```text
btn_blue_back
btn_blue_page
btn_green_primary
btn_red_cancel
btn_yellow_mode_1
btn_white_info_1
enc_main
enc_main_press
```

When the full panel is wired, continue:

```text
btn_yellow_mode_2
btn_white_info_2
btn_green_aux
btn_red_aux
```

The important rule is that IDs never depend on labels printed on the enclosure.

---

## 10. Controller Redis Keys for v0.51

### 10.1 Existing Keys Used

Continue using the controller-owned UI state keys already defined in prior stages.

### 10.2 New Runtime Keys

Add only implementation keys, not architectural substitutes:

```text
rt:input:raw                        (stream or pubsub payload)
rt:input:validated                  (stream or pubsub payload)
rt:exec:wait_slot                   (optional single-slot projection)
rt:exec:pending:<execution_id>
rt:exec:index:pending
rt:exec:lock_state                  (diagnostic only)
rt:exec:stats
rt:panel:<panel_id>:health
rt:panel:<panel_id>:bridge
rt:panel:<panel_id>:led_state
rt:panel:<panel_id>:led_transport
rt:system:runtime_health
```

Keep `rt:input:last_accepted`, `rt:input:last_rejected`, and optional `rt:input:last_result` as derived truth for UI/LED consumers.

---

## 11. Input Pipeline Implementation

### 11.1 `panel_input_bridge.py`

Responsibilities:

- open `/dev/ttyACM*` or configured path
- read newline-delimited frames
- reject oversized or malformed frames
- distinguish `heartbeat`, `led_ack`, and input events
- validate panel identity
- enforce per-session sequence rules
- apply deterministic debounce for button events
- attach ingress metadata:
  - `timestamp`
  - `source=panel.serial`
  - `device_path`
  - `session_id`
- publish normalized raw input event
- maintain `rt:panel:<panel_id>:health`

### 11.2 `input_pipeline.py`

Responsibilities:

- consume normalized raw events
- perform physical mapping
- resolve authoritative interaction state from Redis
- perform semantic mapping by priority:
  - modal
  - transient
  - browse
  - default
- validate allowedIntents and safety
- publish validated intent to execution intake
- record accepted/rejected observability

The bridge and the input pipeline should be separate processes during bring-up so malformed serial traffic cannot contaminate semantic logic.

---

## 12. Execution Engine Implementation

### 12.1 Core Model

The execution engine must implement exactly one serialized execution authority with:

- one immediate intake slot
- one optional wait slot
- one execution lock
- durable pending projections for async work
- one reconciliation path for terminal results

### 12.2 Recommended Internal Components

`execution_engine.py` should contain:

- `ExecutionEngine`
- `Scheduler`
- `ExecutionLock`
- `ExecutionRecordFactory`
- `PendingRegistry`
- `Dispatcher`
- `Reconciler`
- `TimeoutWatchAdapter`

### 12.3 Main Runtime Loop

Recommended structure:

```python
while True:
    msg = validated_intent_subscriber.get()
    scheduler.submit(msg)
    engine.pump()
```

`engine.pump()` should:

1. refuse work if lock held
2. choose next admissible item by policy
3. build execution record
4. acquire execution lock
5. read authoritative state snapshot
6. revalidate family/pending/conflict policy
7. build execution plan
8. apply atomic mutation
9. dispatch downstream only after commit when required
10. write immediate result
11. release lock

### 12.4 Family Policies for v0.51

Implement these initial families:

- `ui_state_navigation`
  - class: state
  - concurrency: serialized only
  - coalescing: yes for navigation delta
  - timeout: none

- `page_transition`
  - class: state/hybrid
  - concurrency: exclusive_global during transition
  - coalescing: no
  - timeout: 5000 ms for lifecycle helpers

- `radio_tune`
  - class: async_action
  - concurrency: exclusive_per_target
  - cancellation: discard_on_completion
  - timeout: 3000 ms

- `system_shutdown`
  - class: maintenance_or_destructive
  - concurrency: exclusive_global
  - cancellation: not_cancellable once committed
  - timeout: phase-specific

### 12.5 Wait Slot Rule

Only navigation delta work may use the replacement wait slot in v0.51.
Everything else is immediate admit or reject.

---

## 13. Atomic Redis Mutation Strategy

Use Redis `MULTI/EXEC` for normal grouped writes.
Use Lua only where atomic read-modify-write is required across keys.

Recommended wrappers:

- `atomic_set_focus()`
- `atomic_set_browse_state()`
- `atomic_replace_modal()`
- `atomic_clear_modal()`
- `atomic_page_cutover()`
- `atomic_register_pending_execution()`
- `atomic_terminalize_execution()`

Do not spread one semantic transition across multiple unguarded writes.

---

## 14. Async Result Reconciliation Implementation

### 14.1 Completion Ingress

Downstream async services must publish completion envelopes that include `execution_id`.

Example:

```json
{
  "execution_id": "8d0c...",
  "intent": "radio.tune",
  "status": "success",
  "completed_at": "2026-03-28T18:00:03Z",
  "target": "rt-radio",
  "payload": {"freq_hz": 14250000}
}
```

### 14.2 `execution_reconciler.py`

Responsibilities:

- consume completion envelopes
- load pending record by `execution_id`
- reject unmatched completions
- acquire execution lock
- confirm record still pending
- perform stale checks
- commit terminal result
- clear pending registry
- publish result + observability

Late success after timeout must be discarded, not applied.

---

## 15. LED Derivation Implementation

### 15.1 `led_deriver.py`

Responsibilities:

- subscribe to `state.changed`, result events, panel health changes
- read authoritative state snapshot
- compute derived booleans
- derive per-control LED meaning by priority
- write canonical LED projection with incrementing `revision`

### 15.2 `panel_led_output.py`

Responsibilities:

- watch `rt:panel:<panel_id>:led_state`
- suppress identical retransmission
- send full snapshot on:
  - panel reconnect
  - revision gap
  - controller restart
- send deltas when possible
- treat missing ack as transport observability only, not semantic failure

### 15.3 Recommended Refresh Policy

- event-driven immediate send on changed LED revision
- periodic full snapshot every 2 seconds while connected
- full snapshot immediately on new `session_id`

This makes reconnects deterministic and keeps the panel synchronized without making the panel authoritative.

---

## 16. Wiring Assumptions

### 16.1 Buttons

Each momentary button contributes:

- 2 switch wires
- 2 LED wires

Recommended initial approach:

- wire switches as GPIO inputs using pull-ups to avoid floating state
- active-low buttons to ground
- controller scans every 5–10 ms in firmware

### 16.2 Encoder

- encoder A → GPIO
- encoder B → GPIO
- encoder push → GPIO active-low

### 16.3 LEDs

If current draw is low and compatible with the board, individual GPIO drive is acceptable for first bring-up.
If not, use transistor drivers or LED driver ICs.

The architectural rule does not care how LEDs are electrically driven.
It only cares that the controller owns the meaning.

### 16.4 USB

Use one USB cable between panel ESP32 and `rt-controller`.
That single cable carries:

- power
- input events
- LED output frames

This is the cleanest first build.

---

## 17. Systemd Units

### 17.1 `rt-panel-input-bridge.service`

```ini
[Unit]
Description=RollingThunder Panel Input Bridge
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-panel-input-bridge.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/panel_input_bridge.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

### 17.2 `rt-input-pipeline.service`

```ini
[Unit]
Description=RollingThunder Input Pipeline
After=redis.service rt-panel-input-bridge.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-input-pipeline.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/input_pipeline.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

### 17.3 `rt-execution-engine.service`

```ini
[Unit]
Description=RollingThunder Execution Engine
After=redis.service rt-input-pipeline.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-execution-engine.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/execution_engine.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

### 17.4 `rt-execution-timeout-watch.service`

```ini
[Unit]
Description=RollingThunder Execution Timeout Watch
After=redis.service rt-execution-engine.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-execution-engine.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/execution_timeout_watch.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

### 17.5 `rt-led-deriver.service`

```ini
[Unit]
Description=RollingThunder LED Deriver
After=redis.service rt-execution-engine.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-led.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/led_deriver.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

### 17.6 `rt-panel-led-output.service`

```ini
[Unit]
Description=RollingThunder Panel LED Output
After=redis.service rt-panel-input-bridge.service rt-led-deriver.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/rt-panel-led-output.env
ExecStart=/usr/bin/python3 /opt/rollingthunder/nodes/rt-controller/services/panel_led_output.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

---

## 18. Environment Files

### 18.1 `/etc/rollingthunder/rt-panel-input-bridge.env`

```bash
RT_REDIS_URL=redis://127.0.0.1:6379/0
RT_PANEL_DEVICE=/dev/ttyACM0
RT_PANEL_DEVICE_GLOB=/dev/ttyACM*
RT_PANEL_ALLOWED_IDS=panel-v1-main
RT_PANEL_BAUD=115200
RT_PANEL_FRAME_MAX=512
RT_PANEL_DEBOUNCE_MS=35
RT_PANEL_HEARTBEAT_STALE_MS=2500
RT_PANEL_OFFLINE_MS=5000
RT_PANEL_RECONNECT_MS=1000
```

### 18.2 `/etc/rollingthunder/rt-execution-engine.env`

```bash
RT_REDIS_URL=redis://127.0.0.1:6379/0
RT_EXEC_DEFAULT_TIMEOUT_MS=3000
RT_EXEC_LOCK_WARN_MS=100
RT_EXEC_TIMEOUT_SWEEP_MS=100
RT_EXEC_CONFIG=/opt/rollingthunder/nodes/rt-controller/config/execution-families.json
```

### 18.3 `/etc/rollingthunder/rt-led.env`

```bash
RT_REDIS_URL=redis://127.0.0.1:6379/0
RT_LED_PANEL_ID=panel-v1-main
RT_LED_FULL_RESEND_MS=2000
```

---

## 19. Bring-Up Order

Follow this exact order.
Do not skip ahead.

### Phase 1 — Firmware-only serial proof

Pass criteria:

- panel enumerates as USB serial device
- heartbeat visible in `screen`/`minicom`
- button press frames appear
- encoder rotate emits `value=+1/-1`
- hold emits once

### Phase 2 — Bridge only

Pass criteria:

- bridge detects panel and creates `session_id`
- health key becomes `online`
- malformed test frames are rejected cleanly
- unplug/replug creates new session and no replay

### Phase 3 — Input pipeline only

Pass criteria:

- raw event becomes canonical action
- canonical action resolves to deterministic intent from current Redis state
- invalid state causes rejection, not guesswork

### Phase 4 — Execution engine synchronous intents

Pass criteria:

- focus next/prev works
- browse enter/exit works
- page next/prev works with wrap
- modal blocks navigation

### Phase 5 — LED derivation

Pass criteria:

- LEDs change only after committed state change
- modal red/green posture correct
- browse blue emphasis correct
- degraded/offline posture correct

### Phase 6 — Async pending and reconciliation

Pass criteria:

- pending execution key created
- pending result published
- completion reconciles correctly
- late completion discarded after timeout

### Phase 7 — Field realism test

Pass criteria:

- knob bursts do not backlog invisibly
- cancel is not starved
- unplug/replug recovers
- restart recovers pending state safely

---

## 20. Deterministic Test Matrix

### 20.1 Input Transport

- valid button press
- duplicate seq
- backward seq
- gap seq
- malformed JSON
- over-512-byte frame
- disconnect during traffic

### 20.2 Mapping

- default NAV_DELTA → focus next/prev
- browse NAV_DELTA → ui.browse.delta
- modal SELECT → ui.ok
- red hold in non-confirmation context stays cancel, not shutdown
- red hold in active confirmation modal resolves to `system.shutdown` only through allowed path

### 20.3 Execution

- repeated page-next during transition rejected
- rapid encoder rotation coalesced into one wait-slot outcome
- cancel after knob burst executes at next safe boundary

### 20.4 LEDs

- rejected input does not fake success LED
- pending action does not show success LED
- controller stale collapses to safe degraded posture
- reconnect causes full LED snapshot resend

### 20.5 Recovery

- restart with pending tune still within deadline
- restart with expired pending tune
- page state valid but focus missing
- browse stale on restart

---

## 21. Minimal Pseudocode

### 21.1 Bridge

```python
for line in serial_lines():
    frame = parse_line(line)
    if not frame.valid:
        reject(frame)
        continue
    if frame.type == "heartbeat":
        update_health(frame)
        continue
    if frame.type == "led_ack":
        record_led_ack(frame)
        continue
    event = normalize_input_frame(frame, session_id)
    if not validate_seq(event):
        reject(event)
        continue
    if debounce_suppressed(event):
        reject(event)
        continue
    publish_raw_event(event)
```

### 21.2 Input Pipeline

```python
for event in raw_event_stream():
    canonical = physical_map(event)
    state = resolve_interaction_state(redis)
    if not state.valid:
        reject_input(event, "state_conflict")
        continue
    intent = semantic_map(canonical, state)
    if not validate_intent(intent, state):
        reject_input(event, "intent_blocked")
        continue
    publish_validated_intent(intent)
```

### 21.3 Execution Engine

```python
for intent in validated_intent_stream():
    outcome = scheduler.admit(intent)
    if outcome.rejected:
        publish_rejection(outcome)
        continue
    next_item = scheduler.next_item()
    with execution_lock:
        record = create_execution_record(next_item)
        plan = build_execution_plan(record, redis)
        result = apply_plan(plan, redis)
        publish_immediate_result(record, result)
```

### 21.4 Reconciler

```python
for completion in completion_stream():
    pending = load_pending(completion.execution_id)
    if not pending:
        publish_unmatched(completion)
        continue
    with execution_lock:
        if is_stale(completion, pending, redis):
            discard_stale(completion)
            continue
        apply_terminal_result(completion, pending, redis)
```

---

## 22. Deployment Assumptions

- Redis already running on `rt-controller`
- app/page config already deployed under `/opt/rollingthunder/config`
- controller and UI stack already operational enough to read state/result events
- radio async completions may initially be simulated by a stub worker until the real downstream target is ready

For first field validation, it is acceptable to stub async completion for `radio.tune` using a deterministic mock publisher.

---

## 23. First Working Milestone Definition

v0.51 is considered working when all of these are true:

1. the wired ESP32 panel connects over USB and emits raw frames
2. the bridge validates and publishes normalized raw events
3. the input pipeline deterministically resolves them into validated intents
4. the execution engine serializes all authoritative state mutation
5. page transitions, browse, focus, modal confirm/cancel, and rejection paths work from hardware
6. LED truth is driven only from authoritative controller state
7. reconnect does not create phantom input or stale LED truth
8. timeout and late-completion behavior are deterministic and observable

---

## 24. Final Implementation Rule

The correct v0.51 build is the one where the panel remains dumb, the controller remains the only authority, every press and rotation still flows through the full intent pipeline, every LED still reflects committed truth rather than optimism, and every async result is either strictly reconciled or safely discarded.

If hardware bring-up introduces a shortcut around those rules, the bring-up is wrong.
