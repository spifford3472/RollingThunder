# Icom IC-2730A — CAT Control Reference (Hamlib)

A complete reference for CAT (Computer Aided Transceiver) commands available on the Icom IC-2730A
using the [Hamlib](https://hamlib.github.io/) library on Raspberry Pi.

---

## Table of Contents

1. [Hardware & Port Setup](#1-hardware--port-setup)
2. [Making the Serial Port Permanent by Name (udev)](#2-making-the-serial-port-permanent-by-name-udev)
3. [Python Boilerplate](#3-python-boilerplate)
4. [CAT Command Reference](#4-cat-command-reference)
   - [Frequency Control](#41-frequency-control)
   - [VFO / Band Selection](#42-vfo--band-selection)
   - [Mode Control](#43-mode-control)
   - [PTT (Transmit Control)](#44-ptt-transmit-control)
   - [Squelch](#45-squelch)
   - [Volume](#46-volume)
   - [Memory Channels](#47-memory-channels)
   - [Signal Strength (S-Meter)](#48-signal-strength-s-meter)
   - [CTCSS / DCS Tones](#49-ctcss--dcs-tones)
   - [Duplex / Repeater Offset](#410-duplex--repeater-offset)
   - [Radio Info](#411-radio-info)
5. [rigctl CLI Quick Reference](#5-rigctl-cli-quick-reference)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Hardware & Port Setup

| Item | Value |
|---|---|
| **Radio** | Icom IC-2730A |
| **Interface** | USB-to-CI-V adapter (e.g. CT-17 compatible) |
| **Default port** | `/dev/ttyUSB1` *(confirmed on this system)* |
| **Baud rate** | 9600 (IC-2730A default) |
| **CI-V address** | `0x90` (IC-2730A default) |
| **Hamlib model** | `RIG_MODEL_IC2730` |

> **Note:** The IC-2730A defaulted to `/dev/ttyUSB1` on this Raspberry Pi.  
> The port assignment can change on reboot if other USB devices are present.  
> See [Section 2](#2-making-the-serial-port-permanent-by-name-udev) to pin it permanently.

---

## 2. Making the Serial Port Permanent by Name (udev)

Without a persistent name, the port can enumerate as `ttyUSB0`, `ttyUSB1`, etc. depending
on plug-in order. A udev rule creates a stable symlink like `/dev/ic2730a` that always
points to the correct adapter regardless of enumeration order.

### Step 1 — Find your adapter's USB attributes

Plug the CI-V USB adapter in, then run:

```bash
udevadm info -a -n /dev/ttyUSB1 | grep -E 'idVendor|idProduct|serial'
```

Note the `idVendor`, `idProduct`, and (if present) `serial` values. Example output:

```
ATTRS{idVendor}=="0403"
ATTRS{idProduct}=="6001"
ATTRS{serial}=="A10BXY2Z"
```

### Step 2 — Create the udev rule

```bash
sudo nano /etc/udev/rules.d/99-ic2730a.rules
```

Paste one of the following (use the serial-based rule if your adapter has a unique serial):

**With serial number (most reliable):**
```
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", \
ATTRS{serial}=="A10BXY2Z", SYMLINK+="ic2730a", MODE="0666"
```

**Without serial number (use only if one adapter):**
```
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", \
SYMLINK+="ic2730a", MODE="0666"
```

> Replace `0403`, `6001`, and `A10BXY2Z` with the values from Step 1.

### Step 3 — Reload udev rules

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### Step 4 — Verify

Unplug and replug the adapter, then confirm the symlink exists:

```bash
ls -l /dev/ic2730a
# Expected: lrwxrwxrwx ... /dev/ic2730a -> ttyUSB1
```

### Step 5 — Update your scripts

Change the port in all scripts from `/dev/ttyUSB1` to `/dev/ic2730a`:

```python
PORT = "/dev/ic2730a"   # permanent symlink — never changes
```

---

## 3. Python Boilerplate

All examples below assume this boilerplate is in scope.

```python
import sys
import Hamlib

PORT      = "/dev/ic2730a"   # permanent udev symlink
BAUD      = 9600
RIG_MODEL = Hamlib.RIG_MODEL_IC2730

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_WARN)  # change to RIG_DEBUG_NONE to silence

rig = Hamlib.Rig(RIG_MODEL)
rig.state.rigport.type.rig              = Hamlib.RIG_PORT_SERIAL
rig.state.rigport.pathname              = PORT
rig.state.rigport.parm.serial.rate      = BAUD
rig.open()
```

Always close the rig when finished:

```python
rig.close()
```

---

## 4. CAT Command Reference

### 4.1 Frequency Control

#### Set Frequency

Sets the operating frequency on the specified VFO. Frequency is in **Hz**.

```python
# Set VFO A to 146.520 MHz (2m calling frequency)
rig.set_freq(Hamlib.RIG_VFO_A, 146.520e6)

# Set VFO B to 446.000 MHz (70cm calling frequency)
rig.set_freq(Hamlib.RIG_VFO_B, 446.000e6)
```

#### Get Frequency

```python
freq_a = rig.get_freq(Hamlib.RIG_VFO_A)
freq_b = rig.get_freq(Hamlib.RIG_VFO_B)

print(f"VFO A: {freq_a / 1e6:.4f} MHz")
print(f"VFO B: {freq_b / 1e6:.4f} MHz")
```

---

### 4.2 VFO / Band Selection

#### Set Active VFO

Switches the active (main) VFO.

```python
# Make VFO A the active VFO
rig.set_vfo(Hamlib.RIG_VFO_A)

# Make VFO B the active VFO
rig.set_vfo(Hamlib.RIG_VFO_B)
```

#### Get Current VFO

```python
current_vfo = rig.get_vfo()
print(f"Current VFO: {current_vfo}")
```

#### VFO Operations (swap, copy, etc.)

```python
# Swap VFO A and VFO B frequencies
rig.vfo_op(Hamlib.RIG_VFO_A, Hamlib.RIG_OP_XCH)

# Copy VFO A frequency to VFO B
rig.vfo_op(Hamlib.RIG_VFO_A, Hamlib.RIG_OP_CPY)

# Toggle VFO (switch between A and B)
rig.vfo_op(Hamlib.RIG_VFO_CURR, Hamlib.RIG_OP_TOGGLE)
```

---

### 4.3 Mode Control

The IC-2730A is a VHF/UHF FM radio; FM is the primary mode. Hamlib still allows
querying and setting mode for completeness.

#### Set Mode

```python
# Set VFO A to FM (normal bandwidth)
rig.set_mode(Hamlib.RIG_MODE_FM, Hamlib.RIG_PASSBAND_NORMAL, Hamlib.RIG_VFO_A)

# Set VFO A to FM with narrow bandwidth (NFM)
rig.set_mode(Hamlib.RIG_MODE_FM, Hamlib.RIG_PASSBAND_NARROW, Hamlib.RIG_VFO_A)
```

#### Get Mode

```python
mode, passband = rig.get_mode(Hamlib.RIG_VFO_A)
print(f"Mode: {Hamlib.rig_strrmode(mode)}  Passband: {passband} Hz")
```

---

### 4.4 PTT (Transmit Control)

> ⚠️ **Caution:** Keying PTT will put the radio on the air. Ensure you are licensed,
> on a clear frequency, and operating legally before enabling transmit.

#### Enable / Disable PTT

```python
# Key the transmitter (PTT on)
rig.set_ptt(Hamlib.RIG_VFO_CURR, Hamlib.RIG_PTT_ON)

# Unkey the transmitter (PTT off)
rig.set_ptt(Hamlib.RIG_VFO_CURR, Hamlib.RIG_PTT_OFF)
```

#### Get PTT State

```python
ptt_state = rig.get_ptt(Hamlib.RIG_VFO_CURR)
print("Transmitting" if ptt_state == Hamlib.RIG_PTT_ON else "Receiving")
```

---

### 4.5 Squelch

#### Set Squelch Level

Squelch level is a float from `0.0` (open) to `1.0` (max squelch).

```python
# Set squelch to mid-level on VFO A
rig.set_level(Hamlib.RIG_LEVEL_SQL, 0.5, Hamlib.RIG_VFO_A)

# Open squelch completely
rig.set_level(Hamlib.RIG_LEVEL_SQL, 0.0, Hamlib.RIG_VFO_A)
```

#### Get Squelch Level

```python
sql_level = rig.get_level_f(Hamlib.RIG_LEVEL_SQL, Hamlib.RIG_VFO_A)
print(f"Squelch: {sql_level:.2f}")
```

---

### 4.6 Volume

#### Set Audio Volume

Volume is a float from `0.0` (muted) to `1.0` (maximum).

```python
# Set volume to 70%
rig.set_level(Hamlib.RIG_LEVEL_AF, 0.7, Hamlib.RIG_VFO_CURR)

# Mute audio
rig.set_level(Hamlib.RIG_LEVEL_AF, 0.0, Hamlib.RIG_VFO_CURR)
```

#### Get Audio Volume

```python
volume = rig.get_level_f(Hamlib.RIG_LEVEL_AF, Hamlib.RIG_VFO_CURR)
print(f"Volume: {volume * 100:.0f}%")
```

---

### 4.7 Memory Channels

#### Set Memory Channel

```python
# Store current VFO settings into memory channel 10
rig.set_mem(Hamlib.RIG_VFO_CURR, 10)
```

#### Get Current Memory Channel

```python
channel = rig.get_mem(Hamlib.RIG_VFO_CURR)
print(f"Current memory channel: {channel}")
```

#### Read a Memory Channel

```python
chan = Hamlib.channel_t()
chan.channel_num = 10
chan.vfo = Hamlib.RIG_VFO_MEM
rig.get_channel(chan, True)

print(f"Memory 10 — Freq: {chan.freq / 1e6:.4f} MHz  Mode: {Hamlib.rig_strrmode(chan.mode)}")
```

#### VFO ↔ Memory Mode

```python
# Switch to memory mode
rig.set_vfo(Hamlib.RIG_VFO_MEM)

# Switch back to VFO mode
rig.set_vfo(Hamlib.RIG_VFO_A)
```

---

### 4.8 Signal Strength (S-Meter)

#### Read S-Meter

Returns signal strength in dBm (integer).

```python
strength = rig.get_level_i(Hamlib.RIG_LEVEL_STRENGTH, Hamlib.RIG_VFO_A)
print(f"Signal: {strength} dBm")
```

#### Read RSSI (Raw)

```python
rssi = rig.get_level_f(Hamlib.RIG_LEVEL_RAWSTR, Hamlib.RIG_VFO_A)
print(f"Raw signal: {rssi}")
```

---

### 4.9 CTCSS / DCS Tones

#### Set CTCSS Tone (Transmit)

Tone frequency is in **tenths of Hz** (e.g. 1318 = 131.8 Hz).

```python
# Enable CTCSS tone of 131.8 Hz on transmit
rig.set_ctcss_tone(Hamlib.RIG_VFO_A, 1318)

# Disable CTCSS tone
rig.set_ctcss_tone(Hamlib.RIG_VFO_A, 0)
```

#### Get CTCSS Tone

```python
tone = rig.get_ctcss_tone(Hamlib.RIG_VFO_A)
print(f"CTCSS Tone: {tone / 10:.1f} Hz" if tone else "No CTCSS tone set")
```

#### Set CTCSS Squelch (Receive)

```python
# Only open squelch on 131.8 Hz subaudible tone
rig.set_ctcss_sql(Hamlib.RIG_VFO_A, 1318)

# Disable CTCSS squelch
rig.set_ctcss_sql(Hamlib.RIG_VFO_A, 0)
```

#### Set DCS Code (Transmit)

```python
# Enable DCS code 023 on transmit
rig.set_dcs_code(Hamlib.RIG_VFO_A, 23)

# Disable DCS
rig.set_dcs_code(Hamlib.RIG_VFO_A, 0)
```

---

### 4.10 Duplex / Repeater Offset

#### Set Repeater Offset Direction

```python
# Positive offset (e.g. +600 kHz for 2m repeater)
rig.set_rptr_shift(Hamlib.RIG_VFO_A, Hamlib.RIG_RPT_SHIFT_PLUS)

# Negative offset (e.g. -5 MHz for 70cm repeater)
rig.set_rptr_shift(Hamlib.RIG_VFO_A, Hamlib.RIG_RPT_SHIFT_MINUS)

# No offset (simplex)
rig.set_rptr_shift(Hamlib.RIG_VFO_A, Hamlib.RIG_RPT_SHIFT_NONE)
```

#### Set Repeater Offset Frequency

Offset is in **Hz**.

```python
# Set +600 kHz offset (standard 2m repeater)
rig.set_rptr_offs(Hamlib.RIG_VFO_A, 600_000)

# Set -5 MHz offset (standard 70cm repeater)
rig.set_rptr_shift(Hamlib.RIG_VFO_A, Hamlib.RIG_RPT_SHIFT_MINUS)
rig.set_rptr_offs(Hamlib.RIG_VFO_A, 5_000_000)
```

#### Get Repeater Settings

```python
shift = rig.get_rptr_shift(Hamlib.RIG_VFO_A)
offset = rig.get_rptr_offs(Hamlib.RIG_VFO_A)
print(f"Shift: {shift}  Offset: {offset / 1e6:.3f} MHz")
```

---

### 4.11 Radio Info

#### Get Radio Capabilities / Info

```python
# Print a summary of the rig's capabilities
print(rig.caps.mfg_name, rig.caps.model_name)
print(f"Hamlib version: {Hamlib.hamlib_version}")
```

#### Check DCD (Carrier Detect / Squelch Open)

Returns whether the squelch is currently open (signal present).

```python
dcd = rig.get_dcd(Hamlib.RIG_VFO_A)
print("Squelch open (signal present)" if dcd == Hamlib.RIG_DCD_ON else "Squelch closed")
```

---

## 5. `rigctl` CLI Quick Reference

`rigctl` is the command-line interface to Hamlib and is great for quick testing without
writing a script. Use the permanent device symlink `/dev/ic2730a`.

```bash
# Interactive session
rigctl -m 3085 -r /dev/ic2730a -s 9600

# One-shot commands (add -m 3085 -r /dev/ic2730a -s 9600 to each)
RADIO="rigctl -m 3085 -r /dev/ic2730a -s 9600"

# Get current frequency
$RADIO f

# Set VFO A frequency to 146.520 MHz
$RADIO F 146520000

# Get mode
$RADIO m

# Set mode to FM normal bandwidth
$RADIO M FM 0

# Get signal strength
$RADIO l STRENGTH

# Set CTCSS tone to 131.8 Hz
$RADIO C 1318

# Enable PTT
$RADIO T 1

# Disable PTT
$RADIO T 0

# Get squelch level
$RADIO l SQL

# Set squelch to 50%
$RADIO L SQL 0.5
```

> **Model number:** `3085` is the numeric ID for `RIG_MODEL_IC2730` in Hamlib.  
> Confirm with: `rigctl --list | grep -i 2730`

---

## 6. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Permission denied` on `/dev/ttyUSB1` | User not in `dialout` group | `sudo usermod -aG dialout $USER` then re-login |
| `Could not open rig` | Wrong port or cable not connected | Check `ls /dev/ttyUSB*`, verify cable |
| Commands time out / no response | Wrong baud rate or CI-V address | Confirm baud in radio menu; default is 9600 |
| Port changes on reboot | No udev rule set | Follow [Section 2](#2-making-the-serial-port-permanent-by-name-udev) |
| `ImportError: No module named Hamlib` | Python bindings not installed | `sudo apt install python3-hamlib` |
| S-meter always reads 0 | Model limitation or wrong VFO | Verify radio is in receive mode; try `RIG_VFO_CURR` |
| Frequency set but readback differs | CI-V echo delay | Add a short `time.sleep(0.1)` before `get_freq` |

---

*Reference compiled for Raspberry Pi OS (Debian 12 Bookworm) with Hamlib 4.x and the Icom IC-2730A.*
