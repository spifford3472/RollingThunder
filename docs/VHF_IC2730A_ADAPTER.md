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

