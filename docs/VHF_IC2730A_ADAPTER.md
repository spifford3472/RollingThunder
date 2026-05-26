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

## Phase 8C-2 — Side-A Direct CI-V Scan/Search Strategy

Phase 8C-2 is an inspection, design, and documentation phase.

No executable direct-CI-V send code was added in this phase.

No CI-V command was sent to the radio in this phase.

No memory write occurred.

No memory bank/group write occurred.

No memory bank/group clear occurred.

No scan start occurred.

No Side B programming occurred.

No PTT/transmit controls were added.

### Files inspected

Phase 8C-2 inspection covered the current VHF/IC-2730A boundary files:

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `config/pages/vhf.json`
- `config/panels/vhf_repeater_scan_summary.json`
- `config/panels/vhf_side_b_summary.json`
- `nodes/rt-display/ui/renderers/vhf_repeater_scan_summary.js`
- `nodes/rt-display/ui/renderers/vhf_side_b_summary.js`
- `docs/VHF_IC2730A_ADAPTER.md`

The local IC-2730A/IC-2730E command reference remains the authoritative source for future direct CI-V work:

- `ic-2730_exmenu-ci-v.pdf`
- Title: `IC-2730A/IC-2730E EXMENU items and CI-V information`

Only IC-2730A/IC-2730E documented CI-V commands from that reference may be used.

Commands must not be inferred from another Icom radio.

Undocumented operations remain `not_implemented`.

### Operator authorization context

The control operator has stated:

- FCC Amateur Extra license
- callsign `KI5VNB`
- physical access to the IC-2730A radio
- control operator present for station operation

Legal/control-operator authorization is not the limiting issue for this phase.

The limiting issue is technical caution and command certainty.

### Hamlib conclusion from 8C-1 / 8C-1B

The Python Hamlib path is not considered reliable for IC-2730A memory, bank/group, or scan automation.

Known findings:

- `RIG_MODEL_IC2730 = 3072`
- previously configured `3085` is IC-705 on this system and should be corrected in a later config-focused phase
- Python Hamlib exposes some generic channel/memory-looking objects and fields
- IC-2730 backend capability calls did not prove usable memory/channel/scan operations
- `get_channel`, `set_channel`, `get_mem`, `set_mem`, and `scan` were not proven usable for this backend
- `scan_ops = 0`
- low-level memory capability calls returned no useful memory capability data

Therefore:

- Python Hamlib memory write remains deferred
- Python Hamlib bank/group programming remains deferred
- Python Hamlib scan control remains deferred
- direct CI-V read-only probing is the safer next path

### Side B / right-side deprecation decision

Adapter-controlled Side B/right-side monitoring is retired from the near-term design.

Reason:

The IC-2730A/IC-2730E EXMENU/CI-V reference states that when using the OPC-478UC connection, audio received on the right side band cannot be heard.

Therefore RollingThunder must not treat the IC-2730A right side as an active adapter-controlled monitored audio resource while CI-V is connected through OPC-478UC.

Near-term rules:

- Do not implement Side B 146.520 adapter programming.
- Do not present Side B as an active monitored right-side audio resource.
- Do not rely on right-side audio while CI-V is connected.
- Do not expose Side B transmit/PTT controls.
- Treat existing `rt:vhf:side_b` model as passive/deprecated unless retained as a compatibility placeholder.
- Future “Side B options” should become Side A/Main-band options where appropriate.

Existing UI Side B renderer behavior is acceptable only as a passive projected model display.

The UI must remain renderer-only and must not program, command, infer, or control the radio.

### Memory write / C-D bank strategy status

The original near-term C/D memory group reload strategy is retired/deferred.

Phase 8A dry-run planning may remain as a controller-side planning artifact until replaced, but real memory programming is not a near-term target.

The IC-2730A/IC-2730E CI-V reference did not document direct CI-V commands for:

- memory channel write
- memory channel 99 selection
- memory bank/group write
- bank/group clear
- bank/group select
- memory/channel name write
- direct scan start/stop
- direct scan-state readback beyond general RX/TX status

Therefore these adapter operations remain `not_implemented`:

- memory write
- memory group/bank clear
- memory group/bank switch
- scan start
- scan stop
- Side B programming

### Banks terminology note

Icom documentation may use “bank” terminology where earlier RollingThunder prompts used “group.”

Terms to search and preserve in future inspections:

- memory group
- group
- memory bank
- bank
- Bank Link
- B-LINK
- Memory Bank
- BND.BNK

The EXMENU document mentions scan/bank-related user-interface features such as Bank Link / B-LINK and banks A-J, but those are not automatically direct CI-V commands.

No bank programming may be implemented unless the IC-2730A/IC-2730E CI-V command table documents the exact direct CI-V command.

### Revised feature direction

RollingThunder should shift toward a Side-A/Main-band repeater scan/search mode.

When the VHF page is active:

- the controller may select a nearby repeater candidate
- the controller may publish a tightly gated adapter request
- the rt-radio adapter may eventually tune/check the IC-2730A Main band / Side A using documented CI-V commands only
- the controller owns dwell timing and candidate advancement
- the UI renders controller-owned state only

When the VHF page is not active:

- the controller must not request adapter-controlled VHF scan/search
- RollingThunder must not take over Side A in the background
- the operator can rely on manually programmed IC-2730A memories/banks/groups

### Candidate future request action

The old near-term request action:

```text
write_single_memory_test

## Phase 8C-3 — Direct CI-V Read-Only Probe

Phase 8C-3 adds a cautious manual direct CI-V read-only probe for the IC-2730A/IC-2730E.

This phase is proof-only. It proves:

- direct CI-V framing
- serial open/close behavior
- command response parsing
- safety readbacks before any future tuning/write/control command is considered

### Files inspected

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`
- local IC-2730A/IC-2730E EXMENU/CI-V reference, when present

### Files changed

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `docs/VHF_IC2730A_ADAPTER.md`

No UI files were changed.

No projector files were changed.

No systemd units were changed.

No controller-side planner or scan-manager behavior was changed.

No Redis request processing behavior was changed.

### Operator authorization context

The control operator has stated:

- FCC Amateur Extra license
- callsign `KI5VNB`
- physical access to the IC-2730A radio
- control operator present for station operation

Legal/control-operator authorization is not the limiting issue.

The limiting issue is technical caution and use of only documented IC-2730A/IC-2730E CI-V commands.

### Config gates

The direct CI-V probe is disabled unless both gates are true:

```json
{
  "vhf": {
    "ic2730a": {
      "direct_civ_enabled": false,
      "direct_civ_readonly_probe_enabled": false,
      "direct_civ_serial_port": "/dev/ic2730a",
      "direct_civ_baud": 9600,
      "direct_civ_controller_address_hex": "E0",
      "direct_civ_transceiver_address_hex": "90",
      "direct_civ_timeout_seconds": 2.0,
      "direct_civ_readonly_probe_commands": [
        "transceiver_id",
        "operating_frequency",
        "operating_mode",
        "duplex",
        "offset",
        "rx_tx_status"
      ]
    }
  }
}

## Phase 8C-3A — Direct CI-V Tone Readback Probe

Phase 8C-3A extends the successful Phase 8C-3 direct CI-V read-only probe with a separate manual CLI-only tone readback probe.

This phase remains read-only.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

### Files inspected

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`
- local IC-2730A/IC-2730E EXMENU/CI-V reference, when present

The implementation uses only the IC-2730A/IC-2730E command families previously recorded from the local reference.

Commands must not be inferred from another Icom radio.

### Operator authorization context

The control operator has stated:

- FCC Amateur Extra license
- callsign `KI5VNB`
- physical access to the IC-2730A radio
- control operator present for station operation

Legal/control-operator authorization is not the limiting issue.

The limiting issue remains technical caution and use of only documented IC-2730A/IC-2730E CI-V commands.

### New manual CLI command

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py \
  --direct-civ-readonly-tone-probe

## Phase 8C-4 Direct CI-V Side-A Dry-Run Request Contract

Phase 8C-4 adds a Redis request/result contract for a future IC-2730A Side-A/Main-band candidate tune/check action.

This phase is dry-run only. It does not send any command to the radio.

### Files inspected

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`

### Files changed

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `docs/VHF_IC2730A_ADAPTER.md`

### Operator authorization context

The station control operator is an FCC Amateur Extra class licensee.

- Callsign: KI5VNB
- Physical access to IC-2730A: yes
- Control operator present: yes

Legal/control-operator authorization is not the limiting factor for this phase. The limiting factor is technical caution and use of only documented IC-2730A/IC-2730E CI-V behavior.

### Prior successful baseline

Phase 8C-3 successfully proved the direct CI-V read-only baseline probe:

```bash
python3 nodes/rt-radio/services/ic2730a_adapter.py --direct-civ-readonly-probe

## Phase 8C-5 Direct CI-V Side-A Readiness Probe Before Any Write

Phase 8C-5 adds a manual CLI-only direct CI-V Side-A/Main-band readiness probe.

This phase remains read-only.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

### Files inspected

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`
- local IC-2730A/IC-2730E EXMENU/CI-V reference, when present

### Files changed

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`

No UI files were changed.

No projector files were changed.

No systemd units were changed.

No controller-side scan-manager behavior was changed.

No Redis request processing behavior was changed.

### Operator authorization context

The station control operator is an FCC Amateur Extra class licensee.

- Callsign: `KI5VNB`
- Physical access to IC-2730A: yes
- Control operator present: yes

Legal/control-operator authorization is not the limiting factor for this phase.

The limiting factor is technical caution and use of only documented IC-2730A/IC-2730E CI-V commands.

### Prior successful baseline

Phase 8C-3 successfully proved the direct CI-V read-only baseline probe:

```bash
python3 nodes/rt-radio/services/ic2730a_adapter.py --direct-civ-readonly-probe


## Phase 8C-6 — Direct CI-V Side-A Candidate Write Plan Only

Phase 8C-6 adds a manual CLI-only direct CI-V Side-A/Main-band candidate write-plan builder.

This phase remains plan-only.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

### Files inspected

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `nodes/rt-radio/services/vhf_ic2730a_adapter_status.py`
- `nodes/rt-controller/services/vhf_repeater_scan_manager.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`
- `/tmp/ic2730a_direct_civ_side_a_readiness_probe.after_8c5.json`, when present
- local IC-2730A/IC-2730E EXMENU/CI-V reference, when present

### Files changed

- `nodes/rt-radio/services/ic2730a_adapter.py`
- `config/app.json`
- `docs/VHF_IC2730A_ADAPTER.md`

No UI files were changed.

No projector files were changed.

No systemd units were changed.

No Redis request service action was added.

### Operator authorization context

The station control operator is an FCC Amateur Extra class licensee.

- Callsign: `KI5VNB`
- Physical access to IC-2730A: yes
- Control operator present: yes

Legal/control-operator authorization is not the limiting factor for this phase.

The limiting factor remains technical caution and use of only documented IC-2730A/IC-2730E CI-V commands.

### Prior successful baseline

Phase 8C-3 successfully proved the direct CI-V read-only baseline probe.

It read:

- transceiver ID using `19 00`
- operating frequency using `03`
- operating mode using `04`
- duplex using `0F`
- offset using `0C`
- RX/TX status using `1C 00`

Phase 8C-3A successfully proved tone readback.

It read:

- tone setting using `1A 00`
- repeater tone frequency using `1B 00`
- tone squelch frequency using `1B 01`
- DTCS code/polarity using `1B 02`

Phase 8C-4 successfully proved the dry-run Side-A candidate request contract.

It validated and echoed candidate payloads without opening serial or sending CI-V.

Phase 8C-5 successfully proved the manual CLI-only Side-A readiness probe.

It read:

- RX/TX status using `1C 00`
- operating frequency using `03`
- operating mode using `04`
- duplex using `0F`
- offset using `0C`
- tone setting using `1A 00`
- repeater tone frequency using `1B 00`
- tone squelch frequency using `1B 01`
- DTCS code/polarity using `1B 02`

### New CLI option

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py \
  --direct-civ-side-a-write-plan \
  --candidate-json '{"frequency_mhz":146.94,"mode":"FM","duplex":"minus","offset_mhz":0.6,"tone_hz":123.0,"tone_mode":"tone"}'

## Phase 8C-7 — First Real Direct CI-V Side-A Tune Test

Phase 8C-7 adds the first manual CLI-only real direct CI-V Side-A/Main-band tune test.

This phase is not automation.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

No memory programming was added.

No scan control was added.

No Side B programming was added.

No PTT/transmit control was added.

### Manual CLI command

```bash
cd /opt/rollingthunder

python3 nodes/rt-radio/services/ic2730a_adapter.py \
  --direct-civ-side-a-real-tune-test \
  --candidate-json '{"frequency_mhz":146.52,"mode":"FM","duplex":"simplex","offset_mhz":0.0,"tone_hz":null,"tone_mode":"none"}'

  ## Phase 8C-8 — First Real Direct CI-V Side-A Repeater Tune Test

Phase 8C-8 adds a separate manual CLI-only direct CI-V Side-A/Main-band repeater-style tune test.

This phase is not automation.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

No memory programming was added.

No scan control was added.

No Side B programming was added.

No PTT/transmit control was added.

### New config gate

The repeater tune test has its own gate:

```json
"direct_civ_side_a_repeater_tune_test_enabled": false

## Phase 8C-9 — Hardened Repeater Tune Duplex Handling

Phase 8C-9 hardens the manual CLI-only direct CI-V Side-A/Main-band repeater-style tune test before any automation is considered.

This phase is not automation.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

No memory programming was added.

No scan control was added.

No Side B programming was added.

No PTT/transmit control was added.

### Phase 8C-8 live success summary

Phase 8C-8 proved a real manual CLI-only direct CI-V repeater-style tune path when the current duplex readback already matched the desired repeater duplex.

Validated live command behavior:

- `07 D0` selected A band as Main.
- `05` wrote operating frequency.
- `0D` wrote 0.600 MHz offset as `00 60 00`.
- `1B 00` wrote repeater CTCSS tone 123.0 Hz as `12 30`.
- `16 42 01` enabled tone encode mode.

Validated live readback and RF behavior:

- Receive frequency: 145.490 MHz
- Mode: FM
- Duplex: DUP-
- Offset: 0.600 MHz
- Repeater CTCSS tone: 123.0 Hz
- Tone mode: tone encode
- Real repeater response confirmed the tone encode path worked.

### Duplex write caution

A separate 146.940 MHz test showed that standalone `11` / DUP- returned CI-V NG.

In that test:

- frequency write succeeded
- standalone `11` / DUP- returned NG
- final readback still showed DUP-
- offset remained 0.000
- tone remained unchanged
- the adapter correctly aborted on NG

Therefore Phase 8C-9 treats duplex as a readback-verified prerequisite, not as a confidently writable setting.

### Phase 8C-9 behavior

The repeater tune test now defers duplex writes.

The test may proceed only when the current duplex readback already matches the candidate duplex.

Examples:

- candidate DUP- and current readback DUP-: proceed with allowed writes if needed
- candidate DUP- and current readback simplex: block before writes
- candidate DUP+ and current readback DUP-: block before writes
- candidate simplex and current readback DUP-: block before writes

When duplex does not already match, the result returns `status=blocked` before write/control commands are sent.

The result summary records:

```json
{
  "duplex_write_deferred": true,
  "duplex_write_required_but_deferred": true,
  "ready_for_future_automation": false
}

## Phase 8C-10 — IC-2730A Direct CI-V Duplex Command Proof

Phase 8C-10 adds a separate, tightly gated, manual CLI-only direct CI-V proof for the documented duplex commands:

- `10` set simplex
- `11` set DUP-
- `12` set DUP+

This phase is not automation.

No Redis request action was added.

No UI behavior was changed.

No projector behavior was changed.

No systemd unit was changed.

No controller-side scan-manager behavior was changed.

No memory programming was added.

No scan control was added.

No Side B programming was added.

No PTT/transmit control was added.

The Phase 8C-9 repeater tune path still defers duplex writes and still requires duplex readback to already match before it proceeds.

### Purpose

Phase 8C-10 exists because Phase 8C-9 intentionally deferred duplex writes after a standalone `11` / DUP- test returned CI-V NG once.

This proof records whether the standalone documented commands `10`, `11`, and `12` are accepted by the IC-2730A in the direct CI-V path and whether readback using `0F` confirms the requested state.

An NG response is a valid proof result.

The proof must not hide NG, retry endlessly, or continue to later sequence steps after a failed command or readback mismatch.

### New config gate

The duplex proof has its own default-false gate:

```json
"direct_civ_side_a_duplex_proof_enabled": false

