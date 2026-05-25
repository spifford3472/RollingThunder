# RollingThunder VHF IC-2730A Adapter Boundary

Phase: RollingThunder v0.55.0 Phase 8C-1

## Purpose

The IC-2730A adapter is the only RollingThunder component that may know IC-2730A / Python Hamlib details.

The adapter boundary exists so controller business logic can ask for safe structured status or send tightly gated adapter requests without knowing:

- Hamlib model numbers
- VFO constants
- serial port details
- CI-V/CAT details
- IC-2730A command behavior
- memory-channel object layout
- memory group behavior
- scan-control behavior

## Physical connection

The IC-2730A is physically connected to `rt-radio`, not `rt-controller`.

The stable device path on `rt-radio` is:

```text
/dev/ic2730a
```

Default Hamlib settings:

```text
model: 3085
baud: 9600
```

## Architecture boundary

RollingThunder uses a dumb-terminal UI model.

The controller owns:

- movement tracking
- dry-run reload planning
- memory group choice
- scan state
- decision to request a reload/write

The `rt-radio` adapter owns:

- IC-2730A details
- Hamlib details
- serial path details
- VFO details
- memory write/group/switch/scan implementation

The UI must not:

- command the radio
- know IC-2730A command details
- calculate distance
- choose C or D
- choose memory channels
- program memories
- start or stop scanning
- infer scan state
- infer adapter state
- expose transmit controls
- expose PTT controls
- scan Redis
- read SQLite

The scan manager must not:

- import Hamlib
- import `ic2730a_adapter.py`
- open serial ports
- shell out to `rigctl`
- know Hamlib model numbers
- know VFO constants
- know CI-V command details
- know IC-2730A raw command details

Only this file may know IC-2730A / Python Hamlib details:

```text
nodes/rt-radio/services/ic2730a_adapter.py
```

Services may write Redis state.

Services may publish `state.changed` to `rt:system:bus`.

Services must not write `rt:ui:bus`.

The projector remains the only writer to `rt:ui:bus`.

## Phase 8B — Controlled single-memory write-test request path

Phase 8B added a request/response path for one explicitly configured sacrificial memory channel test.

Request key:

```text
rt:vhf:adapter:request
```

Last result key:

```text
rt:vhf:adapter:last_result
```

Supported request action:

```text
write_single_memory_test
```

Phase 8B behavior:

- duplicate `request_id` values are ignored
- wrong sacrificial target channel is rejected
- correct sacrificial target returns `dry_run` in `dry_run` mode
- `hamlib_readonly` still detects the IC-2730A safely
- `hamlib_readonly` rejects write requests
- `hamlib_write_test` currently returns `not_implemented` with `operation_performed=false`
- no real memory write occurs
- no scan start occurs
- no Side B programming occurs
- no PTT/transmit controls are added

## Phase 8C-1 — Hamlib memory/group/scan API findings

Phase 8C-1 is inspection/proof only.

This phase inventories the exact Python Hamlib API surface available on `rt-radio` for the IC-2730A before any real C/D memory group reload work.

Phase 8C-1 does not implement:

- full C/D reload automation
- memory group C clear
- memory group D clear
- bulk memory writes
- scan start
- Side B programming
- SkyWarn
- PTT/transmit controls

## Phase 8C-1 diagnostic command

Default status command:

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py
```

Offline Hamlib API inventory:

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py \
  --hamlib-api-inventory \
  | tee /tmp/ic2730a_hamlib_inventory.offline.json
```

Optional connected read-only inventory, only when the IC-2730A is powered on and connected:

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py \
  --hamlib-api-inventory \
  --connected-readonly \
  | tee /tmp/ic2730a_hamlib_inventory.connected.json
```

The connected read-only inventory does not call:

- `set_mem`
- `set_channel`
- `set_freq`
- `set_mode`
- `scan`
- group clear
- group switch
- Side B programming
- PTT/transmit
- `rigctl`

For this IC-2730A/Hamlib combination, `set_vfo(Hamlib.RIG_VFO_A)` may be used only as the documented read-only mapping step before read-only inspection.

## Findings to record

After running the inventory on `rt-radio`, record the actual values here.

### Hamlib constants

- Hamlib version:
- `RIG_MODEL_IC2730`:
- `RIG_MODEL_IC2730A`:
- `RIG_VFO_A`:
- `RIG_VFO_B`:
- `RIG_VFO_MAIN`:
- `RIG_VFO_SUB`:
- `RIG_VFO_MEM`:
- `RIG_MODE_FM`:
- `RIG_DUPLEX_NONE`:
- `RIG_DUPLEX_PLUS`:
- `RIG_DUPLEX_MINUS`:

### Channel object

- `Hamlib.Channel` available:
- visible fields:
- memory number field:
- frequency field:
- mode field:
- tone field:
- offset field:
- duplex field:
- name field:
- group/bank field:

### Rig method inventory

- `get_channel` visible:
- `set_channel` visible:
- `get_mem` visible:
- `set_mem` visible:
- `get_vfo` visible:
- `set_vfo` visible:
- `scan` visible:
- tone methods visible:
- repeater offset methods visible:
- duplex methods visible:

### Caps inventory

- memory/channel capability fields:
- scan capability fields:
- VFO/memory capability fields:
- tone/duplex/repeater capability fields:

### Connected read-only result

- radio opened:
- `set_vfo(RIG_VFO_A)` mapping result:
- method presence after open:
- connected-read-only error, if any:

## API sufficiency decision

Based on the recorded inventory:

- one sacrificial memory write:
  - sufficient / insufficient / unknown
  - exact API path:
- limited inactive group write:
  - sufficient / insufficient / unknown
  - exact API path:
- Side A group switch:
  - sufficient / insufficient / unknown
  - exact API path:
- scan start:
  - sufficient / insufficient / unknown
  - exact API path:
- scan-state confirmation:
  - sufficient / insufficient / unknown
  - exact API path:

If the exact API remains unclear, write/group/switch/scan operations must remain `not_implemented`.

## Safety confirmation

During Phase 8C-1:

- no memory write was performed
- no memory group was cleared
- no bulk memory write was performed
- no scan start was performed
- no Side B programming was performed
- no PTT/transmit controls were added
- no `rt:ui:bus` writes were added
- no `rigctl` subprocess path was added

### Phase 8C-1C Update: IC-2730A/IC-2730E CI-V Reference Found

A local IC-2730A/IC-2730E document was provided:

- `ic-2730_exmenu-ci-v.pdf`
- Title: `IC-2730A/IC-2730E EXMENU items and CI-V information`

This document is IC-2730A/IC-2730E-specific and is acceptable as a command-reference source for documented commands only.

The document confirms the CI-V data format, default transceiver address `90h`, OK/NG response format, and a limited CI-V command table.

Documented CI-V operations include:

| Operation | Candidate CI-V command | IC-2730A documented? | Read-only or write/control | Risk | Approved for future test? | Notes |
|---|---|---:|---|---|---:|---|
| Read operating frequency | `03` | Yes | Read-only | Low | No | Good candidate for future direct-CI-V read-only probe. |
| Read operating mode | `04` | Yes | Read-only | Low | No | Good candidate for future direct-CI-V read-only probe. |
| Send operating frequency | `05` | Yes | Write/control | Medium | No | Could alter Main-band frequency; not approved in this phase. |
| Select FM mode | `06 05` | Yes | Write/control | Medium | No | Could alter Main-band mode; not approved in this phase. |
| Select A band as Main | `07 D0` | Yes | Write/control | Medium | No | Important for side targeting, but side effects must be tested carefully. |
| Select B band as Main | `07 D1` | Yes | Write/control | Medium | No | Important for side targeting, but side effects must be tested carefully. |
| Read frequency offset | `0C` | Yes | Read-only | Low | No | Good candidate after basic read-only probe. |
| Send frequency offset | `0D` | Yes | Write/control | Medium | No | Could alter repeater settings. |
| Read duplex setting | `0F` | Yes | Read-only | Low | No | Useful for verification. |
| Set simplex | `10` | Yes | Write/control | Medium | No | Not approved in this phase. |
| Set DUP- | `11` | Yes | Write/control | Medium | No | Not approved in this phase. |
| Set DUP+ | `12` | Yes | Write/control | Medium | No | Not approved in this phase. |
| Read squelch / S-meter status | `15 ...` | Yes | Read-only | Low | No | Useful for future receiver-state probe. |
| Read transceiver ID | `19 00` | Yes | Read-only | Low | No | Best first direct-CI-V read-only probe candidate. |
| Send/read tone setting | `1A 00` | Yes | Write/read | Medium | No | Not approved for write. |
| Send/read repeater tone frequency | `1B 00` | Yes | Write/read | Medium | No | Not approved for write. |
| Send/read tone squelch frequency | `1B 01` | Yes | Write/read | Medium | No | Not approved for write. |
| Send/read DTCS code/polarity | `1B 02` | Yes | Write/read | Medium | No | Not approved for write. |
| Read RX/TX status | `1C 00` | Yes | Read-only | Low | No | Useful safety check; must never be used to expose transmit control. |
| Memory channel write | Not found | No | Write/control | High | No | Must remain `not_implemented`. |
| Memory channel 99 selection | Not found | No | Write/control | High | No | Sacrificial memory test cannot proceed from this document alone. |
| Bank/group C selection | Not found | No | Write/control | High | No | C/D strategy not supported by this CI-V table. |
| Bank/group D selection | Not found | No | Write/control | High | No | C/D strategy not supported by this CI-V table. |
| Memory group clear | Not found | No | Write/control | High | No | Must remain `not_implemented`. |
| Scan start/stop by CI-V | Not found | No | Write/control | High | No | Must remain `not_implemented`. |
| Scan-state readback | Not found | No | Read-only | Medium | No | No direct scan-state readback found. |
| Memory/channel name write | Not found | No | Write/control | Medium | No | Must remain `not_implemented`. |
| Side B 146.520 programming | Partially possible only as Main-band frequency/mode control | Partially | Write/control | High | No | Requires redesign; right-side audio/control behavior is not safe to assume. |

### Revised Feasibility Summary

Direct CI-V is feasible for a future read-only probe and possibly for limited Main-band frequency/mode/repeater parameter control.

Direct CI-V is not yet feasible for the original C/D memory-group reload strategy because this document does not document memory-channel write commands, memory bank/group selection, memory channel 99 selection, group clear, or CI-V scan start/stop.

The adapter should continue to return `not_implemented` for:

- memory write
- memory group clear
- memory group switch
- scan start
- scan stop
- Side B programming

### Side B / Right-Side Redesign Note

Side B should be redesigned before implementation.

The document shows that CI-V can select A or B band as Main, but the command table does not prove independent left/right-side programming. It suggests control may be Main-band oriented.

The document also states that when using the OPC-478UC connection, audio received on the right side band cannot be heard. Therefore, the current idea of Side B as a simple permanently monitored right-side audio resource is not safe to assume.

Future Side B design should separate:

- physical right-side audio availability
- CI-V controllability
- Main-band selection side effects
- 146.520 MHz monitor intent
- whether Side B is passive display state only, manually configured on the radio, or adapter-controlled

Until this is redesigned, Side B programming should remain disabled.