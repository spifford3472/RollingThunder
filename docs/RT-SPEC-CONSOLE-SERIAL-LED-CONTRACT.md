# RT-SPEC-CONSOLE-SERIAL-LED-CONTRACT.md
## RollingThunder Console Serial LED Control Contract
## Status: Draft for Implementation Alignment

---

# 1. Purpose

This document defines the **serial command contract** from `rt-controller` to the RollingThunder console ESP32 for **LED rendering control**.

This contract exists so the controller can authoritatively command the console’s button LEDs without moving semantic control into the panel firmware.

It is intended to support:

- controller-owned LED state
- controller-owned temporary LED effects
- deterministic console rendering
- future `rt-controller` implementation work

This contract does **not** define:

- intent vocabulary
- controller-side meaning of page or panel state
- panel-side semantic interpretation
- alternate control paths
- emergency rescue BLE path behavior except where explicitly isolated

---

# 2. Architectural Rule

The controller owns meaning.

The console owns only **render mechanics**.

That means:

- `rt-controller` decides which LEDs should be on, off, blinking, pulsing, or temporarily animated
- the console ESP32 only renders those commands
- the console must not infer page, modal, browse, or safety meaning on its own
- the console must not decide that a button is active just because it was pressed

This preserves the existing RollingThunder rule that LEDs reflect controller-owned state, not local hardware guesses.

---

# 3. Scope

This contract covers only:

- serial messages sent from `rt-controller` to console ESP32
- LED control commands
- LED animation helper commands
- capability snapshot transport for LED rendering support
- console response behavior for command validity

This contract does not replace the raw panel event path from console → controller.

Normal panel input still flows:

`console raw event emission -> bridge/controller pipeline -> mapping -> validation -> execution -> controller state`

This document defines only the opposite direction:

`rt-controller -> console serial LED control`

---

# 4. Design Principles

## 4.1 Controller Is Authoritative

Every LED effect shown on the console must originate from an explicit controller command or a controller-owned authoritative state snapshot.

## 4.2 Console Is a Dumb LED Renderer

The console firmware may:

- parse serial LED commands
- store LED render state
- animate LEDs according to controller instructions
- temporarily overlay a controller-requested animation such as `show_push`

The console firmware may not:

- infer LED meaning from button presses
- translate local panel state into LED authority
- reject controller LED commands based on page logic
- invent a blink or pulse because it “seems right”

## 4.3 Non-Blocking Implementation

All LED modes and effects must be implemented with timer/state-machine logic using `millis()` or equivalent.

The console must not use blocking `delay()` for LED animation because it must continue to:

- emit raw input events
- receive serial commands
- service the emergency BLE reboot path

## 4.4 Stable IDs

All button and LED identifiers must be stable symbolic names, not GPIO numbers.

## 4.5 Fail Safe

If the console receives malformed or unsupported LED commands, it must ignore them safely and optionally report an error response.

Malformed controller commands must never crash the console.

---

# 5. Controlled LEDs

The controller may address these symbolic button LEDs:

- `back`
- `page`
- `primary`
- `cancel`
- `mode`
- `info`

These names are the canonical console LED identifiers.

---

# 6. Supported LED Render Modes

The console must support these persistent LED modes:

- `off`
- `on`
- `blink`
- `pulse`

## 6.1 off

LED fully off.

## 6.2 on

LED steady on.

## 6.3 blink

LED toggles between on/off at a controller-specified period.

## 6.4 pulse

LED performs a softer repeating visual effect at a controller-specified period.

Implementation shape is console-local, but must remain visually distinct from `blink`.

---

# 7. One-Shot Effect

The console must support this one-shot temporary effect:

- `show_push`

`show_push` is a controller-commanded visual acknowledgement effect.

It must:

1. remember the LED’s current persistent mode and timing
2. temporarily override the LED output
3. run this sequence:
   - off for 250 ms
   - on for 250 ms
   - off for 250 ms
4. restore the prior persistent LED state

This effect is strictly visual.

It does **not** imply acceptance, success, validity, or semantics unless the controller intentionally uses it for that purpose.

---

# 8. Transport Format

## 8.1 Framing

Controller-to-console LED commands must be:

- UTF-8 text
- one JSON object per line
- newline delimited

This matches the simplicity and observability of the existing panel event transport style.

## 8.2 General Envelope

Every controller-to-console LED message must use this top-level shape:

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "<command>"
}
```

Required fields:

- `schema`
- `type`
- `cmd`

`type` must always be `"led"` for this contract.

---

# 9. Commands

## 9.1 set

Sets one button LED to a persistent render mode.

### Format

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "set",
  "button": "primary",
  "mode": "on"
}
```

### Blink example

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "set",
  "button": "back",
  "mode": "blink",
  "period_ms": 400
}
```

### Pulse example

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "set",
  "button": "info",
  "mode": "pulse",
  "period_ms": 900
}
```

### Rules

Required fields:

- `button`
- `mode`

Optional fields:

- `period_ms` required when `mode` is `blink` or `pulse`

Valid `mode` values:

- `off`
- `on`
- `blink`
- `pulse`

If `mode` is `off` or `on`, `period_ms` is ignored.

If `mode` is `blink` or `pulse` and `period_ms` is missing, the command is invalid.

## 9.2 show_push

Triggers the one-shot temporary push animation on the specified button LED.

### Format

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "show_push",
  "button": "primary"
}
```

### Rules

- `button` is required
- console must preserve and restore previous persistent state
- if the button is already in a `show_push` animation, the new `show_push` restarts the animation from the beginning

## 9.3 all_off

Turns all controlled button LEDs off and clears their persistent render modes.

### Format

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "all_off"
}
```

### Rules

- all LEDs become `off`
- any one-shot animations are cancelled
- any persistent blink/pulse timers are cleared

## 9.4 reset_leds

Returns the console LED subsystem to a clean baseline.

### Format

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "reset_leds"
}
```

### Rules

- equivalent to a subsystem clear
- all LEDs off
- all animations cancelled
- all mode/timing state cleared

This is useful on reconnect, resync, and controller authority recovery.

## 9.5 snapshot

Applies a full authoritative LED snapshot to all buttons at once.

### Format

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "snapshot",
  "leds": {
    "back":    { "mode": "on" },
    "page":    { "mode": "pulse", "period_ms": 900 },
    "primary": { "mode": "blink", "period_ms": 400 },
    "cancel":  { "mode": "off" },
    "mode":    { "mode": "off" },
    "info":    { "mode": "on" }
  }
}
```

### Rules

- `leds` object is required
- each included button entry replaces that button’s entire persistent LED state
- omitted buttons must default to `off`
- all one-shot animations are cancelled before applying snapshot
- snapshot is the preferred command after console reconnect or restart

---

# 10. Optional Capability Command

The controller may optionally send a control capability snapshot to the console for future display or debugging support, but this capability data must not alter semantic input behavior in firmware.

This is included only so `rt-controller` and console code can grow cleanly later.

## 10.1 capability_snapshot

```json
{
  "schema": 1,
  "type": "led",
  "cmd": "capability_snapshot",
  "controls": {
    "back":    { "visible": true, "available": true },
    "page":    { "visible": true, "available": true },
    "primary": { "visible": true, "available": false },
    "cancel":  { "visible": true, "available": true },
    "mode":    { "visible": true, "available": false },
    "info":    { "visible": true, "available": false }
  }
}
```

### Rule

This command is informational only.

The console may store it, but it must not locally suppress button events on the basis of this snapshot during normal architecture-compliant operation.

Controller validation remains authoritative.

---

# 11. Required Console Behavior

## 11.1 Persistent State Table

The console firmware should maintain per-button LED state containing at least:

- current persistent mode
- current timing period
- current output level
- one-shot animation active flag
- saved prior persistent mode
- saved prior timing

## 11.2 Rendering Loop

The console must run a non-blocking periodic update loop that:

- applies persistent modes
- advances blink/pulse timing
- advances `show_push` one-shot animations
- restores prior persistent state after one-shot completion

## 11.3 No Semantic Side Effects

A controller `show_push(primary)` command must not alter:

- button availability
- input suppression
- page meaning
- modal meaning
- local controller state assumptions

It changes only the LED render state.

## 11.4 Serial Parsing

The console must:

- buffer bytes until newline
- parse one JSON object per line
- ignore blank lines
- reject malformed JSON safely
- reject unsupported commands safely

---

# 12. Suggested Console Responses

Responses are optional but recommended.

If implemented, they must also be newline-delimited JSON.

## 12.1 ack

```json
{
  "schema": 1,
  "type": "led_ack",
  "cmd": "set",
  "ok": true
}
```

## 12.2 error

```json
{
  "schema": 1,
  "type": "led_ack",
  "cmd": "set",
  "ok": false,
  "reason": "invalid_mode"
}
```

Recommended `reason` values:

- `json_invalid`
- `schema_invalid`
- `type_invalid`
- `cmd_invalid`
- `button_invalid`
- `mode_invalid`
- `period_missing`
- `payload_invalid`

If serial bandwidth budget is tight, controller acknowledgements may be disabled in production.

---

# 13. Reconnect and Resync Rules

## 13.1 Console Startup

On console startup before controller authority is re-established:

- all LEDs must default to `off`
- no prior LED state may be assumed
- no page meaning may be inferred

## 13.2 Controller Resync

When `rt-controller` reconnects to the console or detects console reset, controller should send:

1. `reset_leds`
2. full `snapshot`

This guarantees that the console LED state is fully reconstructed from controller truth.

## 13.3 Controller Silence

If controller stops sending LED updates, the console must continue rendering the last authoritative persistent state it received.

However, a later higher-level stale-controller rule may require a safe degraded LED collapse. That policy belongs above this low-level render contract.

---

# 14. Interaction with Emergency Recovery Path

The emergency BLE reboot recovery path is a narrowly scoped exception outside the normal controller-owned control plane.

That recovery path may temporarily override the green/red LEDs locally only during the guarded rescue sequence.

Outside that specific rescue posture:

- normal LED state must remain controller-owned
- rescue logic must not become a general-purpose local LED authority
- rescue completion or cancel should return LEDs to the current controller-driven persistent render state or safe off state until refreshed

---

# 15. Controller Implementation Guidance

`rt-controller` should eventually own a console LED output service or subsystem that:

- derives canonical LED meaning from controller state
- projects that into this serial command contract
- sends snapshots on console reconnect
- sends targeted updates for incremental changes
- optionally triggers `show_push` after controller-accepted actions where appropriate

Preferred controller behavior:

- use `snapshot` for initial sync
- use `set` for steady-state incremental updates
- use `show_push` for one-shot UI polish
- use `all_off` or `reset_leds` during authority reset/recovery

---

# 16. Console Firmware Implementation Guidance

Console firmware should implement at least these internal primitives:

- `ledSet(button, mode, period_ms)`
- `ledAllOff()`
- `ledReset()`
- `ledShowPush(button)`
- `ledApplySnapshot(snapshot)`

These are internal firmware mechanics only.

They must not be exposed as semantic decisions.

---

# 17. Deterministic Test Cases

The contract is not complete until these cases work deterministically.

## 17.1 Single LED On

Controller sends `set(primary,on)`.

Expected:
- primary LED steady on
- all others unchanged

## 17.2 Blink

Controller sends `set(back,blink,400)`.

Expected:
- back LED blinks with 400 ms period
- no other LED state changes

## 17.3 Push Animation Restore

Controller first sends `set(primary,on)`, then `show_push(primary)`.

Expected:
- push animation runs
- primary returns to steady on

## 17.4 Snapshot Replace

Controller sends snapshot with only `cancel:on`.

Expected:
- cancel on
- all omitted LEDs off

## 17.5 Reset

Controller sends `reset_leds`.

Expected:
- all LEDs off
- all animations cleared

## 17.6 Malformed Command

Console receives invalid JSON or invalid mode.

Expected:
- safe ignore or explicit error response
- no crash
- no undefined LED behavior

## 17.7 Rescue Coexistence

During local rescue-armed mode, green/red flash locally.
After rescue cancel or completion, controller sends fresh snapshot.

Expected:
- rescue flash ends
- controller snapshot becomes authoritative again

---

# 18. Non-Negotiable Rules

1. Controller owns LED meaning.
2. Console owns only LED render mechanics.
3. Console must not infer page or modal semantics locally.
4. LED render commands must be non-blocking.
5. Stable symbolic button IDs must be used.
6. Omitted buttons in a snapshot must default to off.
7. `show_push` must restore prior persistent state.
8. Malformed LED commands must fail safe.
9. Emergency rescue local override must remain narrowly scoped.
10. This contract must not be treated as permission for general panel-side semantic logic.

---

# 19. Completion Criteria

This contract is complete when:

1. `rt-controller` can send authoritative LED render commands to the console
2. console can render steady and animated LED states deterministically
3. console can resync cleanly after restart/reconnect
4. controller retains semantic authority
5. rescue exception remains isolated from the normal control plane
6. both controller and console teams can implement against the same message contract without ambiguity

---

# Final Rule

The console may render LEDs, blink LEDs, pulse LEDs, and run temporary visual effects only because the controller told it to do so.

If the console starts deciding LED meaning on its own, the contract has been violated.
