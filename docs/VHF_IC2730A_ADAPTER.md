# RollingThunder VHF IC-2730A Adapter Boundary

Phase: RollingThunder v0.55.0 Phase 7B

## Purpose

The IC-2730A adapter is the only RollingThunder component that may know IC-2730A / Python Hamlib details.

The adapter boundary exists so controller business logic can ask for safe structured status without knowing:

- Hamlib model numbers
- VFO constants
- serial port details
- CI-V/CAT details
- IC-2730A command behavior

## Physical connection

The IC-2730A is physically connected to `rt-radio`, not `rt-controller`.

The stable device path on `rt-radio` is:

```text
/dev/ic2730a