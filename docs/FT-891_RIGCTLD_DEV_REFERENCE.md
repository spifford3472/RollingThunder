# FT-891 / Hamlib / rigctld — Developer Reference Guide

**Platform:** Raspberry Pi + DigiRig DR-891 + Yaesu FT-891  
**Hamlib model ID:** 136  
**Default TCP port:** 4532  
**Serial device:** `/dev/ttyUSB0` (verify with `ls /dev/ttyUSB*` after plugging in)

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Installation & Startup](#2-installation--startup)
3. [How to Send and Receive Commands](#3-how-to-send-and-receive-commands)
4. [rigctld Command Reference — Hamlib Layer](#4-rigctld-command-reference--hamlib-layer)
   - 4.1 [VFO & Frequency](#41-vfo--frequency)
   - 4.2 [Mode & Passband](#42-mode--passband)
   - 4.3 [PTT & Transmit Control](#43-ptt--transmit-control)
   - 4.4 [Split Operation](#44-split-operation)
   - 4.5 [Levels — Read & Write](#45-levels--read--write)
   - 4.6 [Functions — Toggle On/Off](#46-functions--toggleonoff)
   - 4.7 [Memory Channels](#47-memory-channels)
   - 4.8 [CW & Voice Keyer](#48-cw--voice-keyer)
   - 4.9 [Band & Parameter Settings](#49-band--parameter-settings)
   - 4.10 [Scanning & Squelch](#410-scanning--squelch)
   - 4.11 [Diagnostic & Utility Commands](#411-diagnostic--utility-commands)
5. [Sending Raw CAT Commands While Hamlib Is Running](#5-sending-raw-cat-commands-while-hamlib-is-running)
6. [Complete FT-891 Native CAT Command Reference](#6-complete-ft-891-native-cat-command-reference)
   - 6.1 [CAT Command Protocol](#61-cat-command-protocol)
   - 6.2 [Core CAT Commands](#62-core-cat-commands)
   - 6.3 [EX Menu Commands — Full Parameter Table](#63-ex-menu-commands--full-parameter-table)
7. [DigiRig DR-891 Notes](#7-digirig-dr-891-notes)
8. [Python Code Examples](#8-python-code-examples)
9. [Quick Answer Reference](#9-quick-answer-reference)

---

## 1. System Architecture Overview

```
FT-891 Radio
    │
    │ (CAT + audio via 6-pin mini-DIN)
    ▼
DigiRig DR-891
    │
    │ (USB — presents as /dev/ttyUSB0 for CAT + USB audio device)
    ▼
Raspberry Pi
    ├── hamlib (FT-891 backend, model 136)
    │       │
    │       ▼
    └── rigctld (TCP daemon on port 4532)
            │
            │ (TCP socket connections)
            ├── WSJT-X / JS8Call / FT8
            ├── Log software (Log4OM, N3FJP, etc.)
            └── Your custom Python/Perl/Node.js app
```

**Key architectural points:**

- `rigctld` holds the single serial connection to the radio. All other software connects to it over TCP, not directly to the serial port. This is the correct multi-client architecture.
- The DigiRig DR-891 provides two separate USB interfaces: a virtual serial port (CAT control, used by rigctld) and a USB audio codec (audio I/O, handled by the OS audio system independently).
- PTT is best handled via CAT command through rigctld (`-P RIG`), avoiding the need for any hardware PTT line.

---

## 2. Installation & Startup

### Install Hamlib

```bash
sudo apt update
sudo apt install hamlib-utils libhamlib-dev
```

For the latest version (recommended — FT-891 support improves with each release):

```bash
sudo apt install cmake build-essential libtool
git clone https://github.com/Hamlib/Hamlib.git
cd Hamlib
autoreconf --install
./configure
make
sudo make install
sudo ldconfig
```

### Identify the USB Serial Port

```bash
ls /dev/ttyUSB*
# or for more detail:
dmesg | grep tty | tail -20
```

Add your user to the dialout group to avoid permission issues:

```bash
sudo usermod -aG dialout $USER
# log out and back in for this to take effect
```

### FT-891 Radio Settings (Required Before Connecting)

Set these in the FT-891 menu before running rigctld:

| Menu Item | Setting |
|-----------|---------|
| 05-06 CAT RATE | 38400 bps (or 9600 — must match `-s` flag below) |
| 05-07 CAT TOT | 100 msec |
| 05-08 CAT RTS | DISABLE (DigiRig handles this differently) |

### Start rigctld

```bash
rigctld -m 136 -r /dev/ttyUSB0 -s 38400 -P RIG -t 4532 &
```

With verbose logging for debugging:

```bash
rigctld -m 136 -r /dev/ttyUSB0 -s 38400 -P RIG -t 4532 -vvvv
```

Useful startup flags:

| Flag | Description |
|------|-------------|
| `-m 136` | FT-891 model number |
| `-r /dev/ttyUSB0` | Serial device |
| `-s 38400` | Baud rate (match menu 05-06) |
| `-P RIG` | PTT via CAT command |
| `-t 4532` | TCP port (default) |
| `-vvvv` | Verbose debug output |
| `--set-conf=retry=5` | Retry count on errors |
| `--set-conf=timeout=2000` | Timeout in ms |
| `--set-conf=disable_yaesu_bandselect=1` | Disable auto band-select on freq change |

### Dump Capabilities (Run This First)

Always verify what your installed hamlib version actually supports for model 136:

```bash
rigctl -m 136 --dump-caps
```

Or while rigctld is running:

```bash
echo "\dump_caps" | nc -w 2 localhost 4532
```

---

## 3. How to Send and Receive Commands

### Protocol Basics

rigctld communicates over a plain TCP socket. Commands are ASCII text terminated with `\n`. Responses are returned as one value per line, terminated with `RPRT 0` (success) or `RPRT -N` (error code).

**Short form:** single character command (e.g., `f`)  
**Long form:** backslash + lowercase name (e.g., `\get_freq`)  
**Convention:** uppercase = SET, lowercase = GET

### From the Command Line (Testing & Scripting)

```bash
# Using rigctl (the command-line client — connects to rigctld)
rigctl -m 2 get_freq
rigctl -m 2 set_freq 14225000

# Using netcat (raw TCP — good for quick tests)
echo "f" | nc -w 1 localhost 4532
echo "F 14225000" | nc -w 1 localhost 4532

# Interactive rigctl session
rigctl -m 2
# Then type commands at the prompt:
#   f       (get frequency)
#   F 14074000
#   m       (get mode)
#   M USB 2400
#   q       (quit)
```

### Extended Response Protocol

Prefix any command with `+` to get key:value formatted responses — recommended for client applications:

```bash
echo "+f" | nc -w 1 localhost 4532
# Returns:
# get_freq:
# Frequency: 14225000
# RPRT 0
```

### From Python

```python
import socket

def rigctld_command(cmd, host='localhost', port=4532):
    """Send a command to rigctld and return the response."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        s.sendall((cmd + '\n').encode())
        response = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
            if b'RPRT' in response:
                break
    return response.decode().strip()

# Examples
print(rigctld_command('f'))             # get frequency
print(rigctld_command('F 14074000'))    # set frequency
print(rigctld_command('m'))             # get mode
print(rigctld_command('M PKTUSB 3000')) # set mode for FT8
print(rigctld_command('l STRENGTH'))    # read S-meter
print(rigctld_command('T 1'))           # PTT on
print(rigctld_command('T 0'))           # PTT off
```

### Persistent Connection (Recommended for Applications)

For applications that poll the radio frequently, maintain a persistent TCP connection rather than opening a new socket per command:

```python
import socket
import threading

class RigctldClient:
    def __init__(self, host='localhost', port=4532):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.lock = threading.Lock()

    def command(self, cmd):
        with self.lock:
            self.sock.sendall((cmd + '\n').encode())
            response = b''
            while True:
                chunk = self.sock.recv(4096)
                response += chunk
                if b'RPRT' in response:
                    break
            return response.decode().strip()

    def close(self):
        self.sock.close()

rig = RigctldClient()
print(rig.command('f'))         # get freq
print(rig.command('l SWR'))     # read SWR
rig.close()
```

### Error Codes

rigctld returns `RPRT -N` where N is:

| Code | Meaning |
|------|---------|
| 0 | Success |
| -1 | Invalid parameter |
| -2 | Invalid configuration |
| -3 | Memory shortage |
| -4 | Feature not implemented |
| -5 | Communication error |
| -6 | IO error |
| -7 | Internal error |
| -8 | Protocol error |
| -9 | Command rejected by rig |
| -10 | Argument out of domain |
| -11 | Invalid VFO |
| -12 | Disabled (not available) |

---

## 4. rigctld Command Reference — Hamlib Layer

**Command syntax:** `SHORT_CMD [args]` or `\long_cmd_name [args]`  
**Uppercase** = set/write, **lowercase** = get/read

### 4.1 VFO & Frequency

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `F <Hz>` | `\set_freq` | Write | Set VFO frequency in Hz | `F 14225000` |
| `f` | `\get_freq` | Read | Get current frequency + active VFO | `f` |
| `V <vfo>` | `\set_vfo` | Write | Set active VFO | `V VFOA` |
| `v` | `\get_vfo` | Read | Get current VFO | `v` |
| `J <Hz>` | `\set_rit` | Write | Set RIT offset in Hz; `0` = reset | `J +500` |
| `j` | `\get_rit` | Read | Get RIT offset in Hz | `j` |
| `Z <Hz>` | `\set_xit` | Write | Set XIT offset in Hz; `0` = reset | `Z -200` |
| `z` | `\get_xit` | Read | Get XIT offset in Hz | `z` |
| `N <Hz>` | `\set_ts` | Write | Set tuning step in Hz | `N 100` |
| `n` | `\get_ts` | Read | Get current tuning step | `n` |

VFO tokens: `VFOA`, `VFOB`, `VFOC`, `currVFO`, `MEM`, `Main`, `Sub`, `TX`, `RX`

> **Note on RIT/XIT:** RIT and XIT must be explicitly activated/deactivated using `\set_func RIT 1` / `\set_func XIT 1`. Setting the offset to 0 does not disable the function.

### 4.2 Mode & Passband

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `M <mode> <bw>` | `\set_mode` | Write | Set mode and passband in Hz | `M USB 2400` |
| `m` | `\get_mode` | Read | Get current mode and passband | `m` |

Mode tokens: `USB`, `LSB`, `CW`, `CWR`, `RTTY`, `RTTYR`, `AM`, `FM`, `PKTUSB`, `PKTLSB`, `PKTFM`

Passband: Hz integer, `0` = radio default, `-1` = no change

Common mode+passband combinations for FT-891:

| Mode | Command | Use |
|------|---------|-----|
| SSB voice | `M USB 2400` | Normal SSB |
| FT8 / FT4 / WSPR | `M PKTUSB 3000` | Digital modes via audio |
| JS8Call | `M PKTUSB 3000` | JS8 digital |
| CW narrow | `M CW 500` | CW contest |
| CW wide | `M CW 2400` | CW casual |
| AM | `M AM 6000` | AM broadcast listening |
| FM | `M FM 15000` | FM voice |

Send `M ?` to get a list of all modes the backend supports.

### 4.3 PTT & Transmit Control

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `T <0/1/2/3>` | `\set_ptt` | Write | Set PTT state | `T 1` |
| `t` | `\get_ptt` | Read | Get PTT state | `t` |

PTT values: `0` = RX, `1` = TX, `2` = TX via mic, `3` = TX data

> **DigiRig note:** With `-P RIG`, PTT is sent as a CAT command to the radio. This is the recommended method with the DigiRig DR-891.

### 4.4 Split Operation

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `S <0/1> <txvfo>` | `\set_split_vfo` | Write | Enable/disable split, set TX VFO | `S 1 VFOB` |
| `s` | `\get_split_vfo` | Read | Get split state and TX VFO | `s` |
| `I <Hz>` | `\set_split_freq` | Write | Set TX frequency | `I 14230000` |
| `i` | `\get_split_freq` | Read | Get TX frequency | `i` |
| `X <mode> <bw>` | `\set_split_mode` | Write | Set TX mode and passband | `X USB 2400` |
| `x` | `\get_split_mode` | Read | Get TX mode and passband | `x` |

### 4.5 Levels — Read & Write

**Syntax:** `L <token> <value>` to set, `l <token>` to read

| Token | R/W | Description | Range |
|-------|-----|-------------|-------|
| `RFPOWER` | R/W | TX power setting (normalized) | 0.0 – 1.0 |
| `RFPOWER_METER` | Read | Live RF power meter reading | float |
| `RFPOWER_METER_WATTS` | Read | Live RF power in watts | float |
| `AF` | R/W | AF (audio) gain | 0.0 – 1.0 |
| `RF` | R/W | RF gain | 0.0 – 1.0 |
| `SQL` | R/W | Squelch level | 0.0 – 1.0 |
| `IF` | R/W | IF shift | Hz, signed |
| `NR` | R/W | Noise reduction level | 0.0 – 1.0 |
| `NB` | R/W | Noise blanker level | 0.0 – 1.0 |
| `MICGAIN` | R/W | Microphone gain | 0.0 – 1.0 |
| `COMP` | R/W | Speech compressor level | 0.0 – 1.0 |
| `COMP_METER` | Read | Compression meter | float |
| `AGC` | R/W | AGC speed | 0=OFF, 2=FAST, 3=SLOW, 5=MID, 6=AUTO |
| `KEYSPD` | R/W | CW keyer speed | WPM (integer) |
| `CWPITCH` | R/W | CW sidetone pitch | Hz |
| `BKINDL` | R/W | Semi break-in delay | ms |
| `NOTCHF` | R/W | Manual notch frequency | Hz |
| `VOXGAIN` | R/W | VOX sensitivity | 0.0 – 1.0 |
| `VOXDELAY` | R/W | VOX hang time | ms |
| `ANTIVOX` | R/W | Anti-VOX level | 0.0 – 1.0 |
| `MONITOR_GAIN` | R/W | TX monitor level | 0.0 – 1.0 |
| `METER` | R/W | Active meter display | token |
| `ALC` | Read | ALC meter | float |
| `SWR` | Read | SWR meter | float (1.0 = 1:1) |
| `STRENGTH` | Read | S-meter (calibrated) | dB relative to S9 |
| `RAWSTR` | Read | Raw S-meter ADC value | integer |
| `ID_METER` | Read | PA current | float |
| `BAND_SELECT` | R/W | Band selection | token (e.g. `BAND20M`) |

Examples:
```
L RFPOWER 0.5       # set TX power to 50%
l RFPOWER           # read power setting
l SWR               # read SWR
l ALC               # read ALC
l STRENGTH          # read S-meter (e.g. returns -20 for S7)
L AGC 3             # set AGC to SLOW
L KEYSPD 20         # set CW speed to 20 WPM
l RFPOWER_METER_WATTS  # read live TX power in watts
```

### 4.6 Functions — Toggle On/Off

**Syntax:** `U <token> <1/0>` to set, `u <token>` to read

Send `U ?` to get a list of all function tokens the backend supports.

| Token | Description |
|-------|-------------|
| `TUNER` | **ATU tuner on/off — `U TUNER 1` triggers the ATU tune cycle** |
| `NB` | Noise blanker on/off |
| `NR` | Noise reduction on/off |
| `VOX` | VOX on/off |
| `FBKIN` | Full break-in (QSK) CW on/off |
| `SBKIN` | Semi break-in CW on/off |
| `COMP` | Speech compressor on/off |
| `ANF` | Automatic notch filter on/off |
| `MN` | Manual notch filter on/off |
| `MON` | TX monitor on/off |
| `TONE` | CTCSS tone encode on/off |
| `TSQL` | CTCSS tone squelch on/off |
| `LOCK` | VFO/panel lock on/off |
| `APF` | Audio peak filter on/off |
| `RIT` | RIT on/off (use after setting RIT offset with `J`) |
| `XIT` | XIT on/off (use after setting XIT offset with `Z`) |

Examples:
```
U TUNER 1     # start ATU tune cycle
u TUNER       # read tuner state
U NR 1        # enable noise reduction
U NB 1        # enable noise blanker
U FBKIN 1     # enable QSK full break-in
U LOCK 1      # lock the front panel
U RIT 1       # activate RIT (set offset first with J)
```

> **ATU Tune Cycle:** `U TUNER 1` sends the appropriate Yaesu CAT command to activate the internal ATU. The radio will key up briefly (you must be transmitting or the radio must be in tune mode) to find an impedance match. Poll `u TUNER` to detect completion.

### 4.7 Memory Channels

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `E <n>` | `\set_mem` | Write | Select memory channel | `E 5` |
| `e` | `\get_mem` | Read | Get current memory channel | `e` |
| `G <token>` | `\vfo_op` | Write | VFO/memory operations | `G TO_VFO` |

VFO op tokens: `CPY` (copy VFO-A→B), `XCHG` (swap VFOs), `TO_VFO` (mem→VFO), `FROM_VFO` (VFO→mem), `UP` (step up), `DOWN` (step down), `TUNE` (start tune)

### 4.8 CW & Voice Keyer

| Short | Long Name | Description | Example |
|-------|-----------|-------------|---------|
| `b <text>` | `\send_morse` | Send CW text (up to 50 chars), or memory number 1–5 | `b CQ DE W1ABC` |
| `0xbb` | `\stop_morse` | Stop current morse transmission | `0xbb` |
| `0xbc` | `\wait_morse` | Wait for morse to finish (full break-in only) | |
| `0x94 <n>` | `\send_voice_mem` | Transmit stored voice memory number n | `0x94 1` |

Yaesu-specific: send `b 1` through `b 5` to play CW keyer memory slots 1–5.

### 4.9 Band & Parameter Settings

| Short | Long Name | Dir | Description | Example |
|-------|-----------|-----|-------------|---------|
| `P <parm> <val>` | `\set_parm` | Write | Set rig parameter | `P BANDSELECT BAND20M` |
| `p <parm>` | `\get_parm` | Read | Read rig parameter | `p BANDSELECT` |

Send `P ?` to list all supported parameter tokens.

Common parameter tokens for FT-891: `BANDSELECT`, `BEEP`, `BACKLIGHT`, `ANN`

Band select tokens: `BAND160M`, `BAND80M`, `BAND40M`, `BAND30M`, `BAND20M`, `BAND17M`, `BAND15M`, `BAND12M`, `BAND10M`, `BAND6M`

### 4.10 Scanning & Squelch

| Short | Long Name | Dir | Description |
|-------|-----------|-----|-------------|
| `0x8b` | `\get_dcd` | Read | Squelch/DCD state: `0`=closed, `1`=open |
| `R <+/->` | `\set_rptr_shift` | Write | Repeater shift: `+`, `-`, or other for none |
| `r` | `\get_rptr_shift` | Read | Get repeater shift |
| `O <Hz>` | `\set_rptr_offs` | Write | Repeater offset in Hz |
| `o` | `\get_rptr_offs` | Read | Get repeater offset in Hz |
| `C <tenths>` | `\set_ctcss_tone` | Write | CTCSS tone in tenths of Hz (e.g. `1318` = 131.8 Hz) |
| `c` | `\get_ctcss_tone` | Read | Get CTCSS tone |

### 4.11 Diagnostic & Utility Commands

| Command | Description |
|---------|-------------|
| `\dump_caps` | Dump all backend capabilities — run this to verify support |
| `\dump_state` | Dump current rig state |
| `1` | Get rig info string (manufacturer, model, firmware version) |
| `\get_powerstat` | Get power on/off state |
| `\set_powerstat <0/1>` | Power radio on or off (if supported) |
| `\chk_vfo` | Check if VFO mode is enabled |
| `w <cmd> <timeout_ms>` | **Send raw CAT command string directly to the radio** |
| `W <cmd> <timeout_ms>` | Send raw binary command to the radio |

---

## 5. Sending Raw CAT Commands While Hamlib Is Running

**Yes — you can send raw CAT commands to the FT-891 while rigctld is connected and running**, using the `w` (lowercase) pass-through command. You do not need to disconnect hamlib.

> **Important:** rigctld serializes all access to the serial port. When you use `w`, it queues the raw command through the same connection hamlib is using, so there are no conflicts. Do not attempt to open `/dev/ttyUSB0` directly from another process — that will conflict with rigctld.

### Syntax

Via rigctl:
```bash
rigctl -m 2 w "FA014074000;" 500
#                             ^^^--- timeout in ms
```

Via TCP socket (netcat):
```bash
echo 'w FA014074000; 500' | nc -w 2 localhost 4532
```

Via Python:
```python
response = rigctld_command('w FA014074000; 500')
```

### When to Use Raw CAT

Use raw CAT commands for anything not exposed by hamlib's standard command set:
- All FT-891 internal menu settings (the `EX` command)
- Dimmer/backlight control (`DA`)
- Encoder simulation (`ED`, `EU`) — simulates turning the main knob
- Contest number entry (`EX0406`)
- Reference frequency adjustment (`EX0517`)
- Any function where hamlib returns `RPRT -4` (not implemented)

### Raw CAT Examples

```bash
# Read current VFO-A frequency
rigctl -m 2 w "FA;" 500

# Set VFO-A to 14.074 MHz (FT8)
rigctl -m 2 w "FA014074000;" 500

# Read mode
rigctl -m 2 w "MD0;" 500

# Set mode to DATA-USB (mode 8 = Data)
rigctl -m 2 w "MD08;" 500

# Trigger ATU tune
rigctl -m 2 w "AC002;" 500

# Read S-meter
rigctl -m 2 w "SM0;" 500

# Read a menu setting (e.g., menu 05-06 CAT RATE)
rigctl -m 2 w "EX0506;" 500

# Set menu 05-06 CAT RATE to 38400 (value 3)
rigctl -m 2 w "EX05063;" 500

# Set DATA mode digital settings (FT8/JS8 preparation macro)
rigctl -m 2 w "EX08011;" 500   # DATA MODE = OTHERS
rigctl -m 2 w "MD08;" 500      # Set to DATA-USB mode

# Simulate turning the main encoder up 5 steps
rigctl -m 2 w "EU005;" 500

# Simulate turning the main encoder down 10 steps  
rigctl -m 2 w "ED010;" 500
```

---

## 6. Complete FT-891 Native CAT Command Reference

*Source: Yaesu FT-891 CAT Operation Reference Book (ENG rev 1909-C)*

### 6.1 CAT Command Protocol

All FT-891 CAT commands follow this format:

```
COMMAND [PARAMETERS] ;
```

- Commands are exactly **2 alphabetical characters** (upper or lower case accepted)
- All commands are **terminated with a semicolon `;`**
- **Set command:** sends parameters to the radio (e.g., `FA014250000;`)
- **Read command:** sends command with no parameters to request a value (e.g., `FA;`)
- **Answer command:** the radio's response to a Read command (e.g., `FA014250000;`)
- Parameter lengths are fixed — every digit position must be filled exactly

**Auto Information (AI) mode:** When `AI1;` is sent, the radio automatically pushes state changes to the host without polling. Reset with `AI0;` — note that AI mode is automatically disabled when the radio is powered off.

### 6.2 Core CAT Commands

#### VFO / Frequency

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `FA` | VFO-A frequency | `FA014250000;` | `FA;` | 9-digit Hz |
| `FB` | VFO-B frequency | `FB014250000;` | `FB;` | 9-digit Hz |
| `AB` | VFO-A → VFO-B copy | `AB;` | — | Set only |
| `BA` | VFO-B → VFO-A copy | `BA;` | — | Set only |
| `SV` | Swap VFO-A and VFO-B | `SV;` | — | Set only |
| `VM` | VFO-A → memory channel | `VM;` | — | Set only |
| `AM` | VFO-A → current memory | `AM;` | — | Set only |
| `MA` | Memory channel → VFO-A | `MA;` | — | Set only |

#### Band & Channel Navigation

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `BS` | Band select | `BS05;` | — | 00=1.8M, 01=3.5M, 03=7M, 04=10M, 05=14M, 06=18M, 07=21M, 08=24.5M, 09=28M, 10=50M, 11=GEN, 12=MW |
| `BD` | Band down | `BD0;` | — | Cycles to next lower band |
| `BU` | Band up | `BU0;` | — | Cycles to next higher band |
| `MC` | Memory channel select | `MC005;` | `MC;` | 001–900 |
| `CH` | Channel up/down | `CH0;` / `CH1;` | — | 0=up, 1=down |
| `MR` | Memory read | — | `MR0005;` | Read-only |
| `MW` | Memory write | `MW0005...;` | — | Write-only |
| `MT` | Memory write with tag | `MT...;` | — | Write-only |
| `QI` | QMB store | `QI;` | — | Quick memory bank store |
| `QR` | QMB recall | `QR;` | — | Quick memory bank recall |

#### Mode

| Cmd | Function | Set Format | Read | Mode values |
|-----|----------|------------|------|-------------|
| `MD` | Mode | `MD08;` | `MD0;` | 1=LSB, 2=USB, 3=CW, 4=FM, 5=AM, 6=RTTY-LSB, 7=CW-R, 8=DATA, 9=RTTY-USB, D=PKT-FM |
| `NA` | Narrow filter | `NA01;` | `NA0;` | P1=0(fixed), P2=0(OFF)/1(ON) |

#### Transmit & PTT

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `TX` | TX set | `TX0;` (PTT CAT on) | `TX;` | P1=0 for CAT TX |
| `MX` | MOX set | `MX0;` | `MX;` | MOX toggle |
| `PC` | Power control | `PC100;` | `PC;` | 000–100 (watts for FT-891 = 0–100W) |
| `ST` | Split on/off | `ST0;`/`ST1;` | `ST;` | 0=OFF, 1=ON |
| `QS` | Quick split | `QS;` | — | Activates quick split |
| `PS` | Power switch | `PS1;`/`PS0;` | — | Power on/off |

#### Antenna Tuner

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `AC` | Antenna tuner control | `AC002;` | `AC;` | P3: 0=OFF, 1=ON, **2=Start tuning** |

To trigger a tune cycle: `AC002;`  
To read tuner state: `AC;` → returns `AC0P3;` where P3=0(off), 1(on)

#### Receiver Controls

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `AG` | AF gain | `AG0100;` | `AG0;` | P1=0(fixed), P2=000–255 |
| `RG` | RF gain | `RG0255;` | `RG0;` | P1=0(fixed), P2=000–255 |
| `SQ` | Squelch | `SQ0050;` | `SQ0;` | P1=0(fixed), P2=000–255 |
| `IS` | IF shift | `IS0+1000;` | `IS0;` | Signed Hz: `-1400` to `+1400` |
| `PA` | Pre-amp (IPO) | `PA01;` | `PA0;` | P1=0(fixed), P2=0(IPO)/1(AMP1)/2(AMP2) |
| `RA` | RF attenuator | `RA00;` | `RA;` | P1=0(OFF)/1(6dB)/2(12dB)/3(18dB) |
| `GT` | AGC function | `GT03;` | `GT0;` | P2=0(AUTO)/1(FAST)/2(MID)/3(SLOW)/4(OFF) |
| `NB` | Noise blanker | `NB01;` | `NB0;` | P1=0(fixed), P2=0(OFF)/1(ON) |
| `NL` | Noise blanker level | `NL0050;` | `NL0;` | P2=000–100 |
| `NR` | Noise reduction | `NR01;` | `NR;` | P1=0(OFF)/1(NR-1)/2(NR-2) |
| `RL` | NR level | `RL01;` | `RL;` | 01–15 |
| `BC` | Auto notch | `BC01;` | `BC0;` | P1=0(fixed), P2=0(OFF)/1(ON) |
| `BP` | Manual notch | `BP00001;` | `BP00;` | P2=0(on/off)/1(level), P3=000–320 |
| `CO` | Contour/APF | `CO00001;` | `CO00;` | See full parameter table |
| `CF` | CLAR (RIT) | `CF010;` | `CF0;` | P2=0(OFF)/1(ON) |
| `RC` | CLAR clear | `RC;` | — | Zero the CLAR offset |
| `RU` | CLAR up | `RU0010;` | — | Increment CLAR |
| `RD` | CLAR down | `RD0010;` | — | Decrement CLAR |

#### Transmit Audio

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `MG` | Mic gain | `MG050;` | `MG;` | 000–100 |
| `ML` | Monitor level | `ML0100;` | `ML0;` | P1=0(fixed), P2=000–100 |
| `PL` | Speech processor level | `PL050050;` | `PL;` | P1=INPUT(000–100), P2=OUTPUT(000–100) |
| `PR` | Speech processor | `PR1;` | `PR;` | 0=OFF, 1=ON |
| `VX` | VOX | `VX1;` | `VX;` | 0=OFF, 1=ON |
| `VG` | VOX gain | `VG050;` | `VG;` | 000–100 |
| `VD` | VOX delay | `VD0500;` | `VD;` | 0030–3000 ms (10ms steps) |

#### CW

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `KS` | Key speed | `KS020;` | `KS;` | 004–060 WPM |
| `KP` | Key pitch | `KP12;` | `KP;` | 00–29 (400–1000Hz, 20Hz steps) |
| `KR` | Keyer on/off | `KR1;` | `KR;` | 0=OFF, 1=ON |
| `KY` | CW keying (send text) | `KY0 CQ DE W1ABC ;` | — | Up to 28 chars; P1=0(stop)/1(immed) |
| `BI` | Break-in | `BI1;` | `BI;` | 0=OFF, 1=ON |
| `SD` | Semi break-in delay | `SD0100;` | `SD;` | 0030–3000 ms |
| `KM` | Keyer memory | `KM1text...;` | `KM1;` | Memory 1–5 |
| `ZI` | Zero-in (CW auto) | `ZI;` | — | Set only |
| `CS` | CW spot | `CS1;` | `CS;` | 0=OFF, 1=ON |

#### Repeater / CTCSS

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `OS` | Offset (repeater shift) | `OS00;` | `OS;` | P1=0(OFF)/1(+)/2(-)/3(MANUAL) |
| `CT` | CTCSS | `CT01;` | `CT0;` | P2=0(OFF)/1(ENC+DEC)/2(ENC)/3(DCS) |
| `CN` | CTCSS tone number | `CN0020;` | `CN00;` | See CTCSS table below |

#### Scan

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `SC` | Scan | `SC1;` | `SC;` | 0=stop, 1=up, 2=down |

#### Meters

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `RM` | Read meter | — | `RM1;` (or RM2–RM6) | 1=S-meter, 2=PO, 3=ALC, 4=COMP, 5=SWR, 6=ID |
| `SM` | S-meter | — | `SM0;` | Returns 4-digit value 0000–0030 |
| `MS` | Meter switch | `MS1;` | `MS;` | TX meter: 1=PO, 2=ALC, 3=SWR, 4=COMP, 5=ID |

#### Display & UI

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `DA` | Dimmer | `DA0810080808;` | `DA;` | P1=contrast(01–15), P2=backlight(01–15), P3=LCD(00–15), P4=TX/BUSY(00–15) |
| `ED` | Encoder down | `ED005;` | — | P1=0(main)/8(multi), P2=steps(01–99) |
| `EU` | Encoder up | `EU005;` | — | P1=0(main)/8(multi), P2=steps(01–99) |
| `EK` | Enter key | `EK;` | — | Simulate pressing ENT |
| `DN` | Mic down key | `DN;` | — | Mic down button |
| `UP` | Up key | `UP;` | — | Up button |

#### System

| Cmd | Function | Set Format | Read | Notes |
|-----|----------|------------|------|-------|
| `ID` | Identification | — | `ID;` | Returns radio model ID |
| `IF` | Information (full status) | — | `IF;` | Returns comprehensive status string |
| `RI` | Radio information | — | `RI;` | Detailed status |
| `RS` | Radio status | — | `RS;` | Basic status |
| `BY` | Busy (squelch) | — | `BY;` | P1=0(closed)/1(open) |
| `UL` | PLL unlock status | — | `UL;` | P1=0(locked)/1(unlocked) |
| `AI` | Auto information | `AI1;`/`AI0;` | `AI;` | Push notifications from radio |
| `LK` | Lock | `LK1;` | `LK;` | 0=unlock, 1=lock |
| `FS` | Fast step | `FS1;` | `FS;` | 0=OFF, 1=ON |
| `TS` | TXW | `TS1;` | `TS;` | TX Watch |
| `PB` | Play back (DVS) | `PB0;` | — | P1=0–5 for DVS memories |
| `LM` | Load message (DVS) | `LM1;` | — | Load DVS message |

#### CTCSS Tone Table (for `CN` command)

| Index | Freq | Index | Freq | Index | Freq |
|-------|------|-------|------|-------|------|
| 000 | 67.0 Hz | 017 | 118.8 Hz | 034 | 183.5 Hz |
| 001 | 69.3 Hz | 018 | 123.0 Hz | 035 | 186.2 Hz |
| 002 | 71.9 Hz | 019 | 127.3 Hz | 036 | 189.9 Hz |
| 003 | 74.4 Hz | 020 | 131.8 Hz | 037 | 192.8 Hz |
| 004 | 77.0 Hz | 021 | 136.5 Hz | 038 | 196.6 Hz |
| 005 | 79.7 Hz | 022 | 141.3 Hz | 039 | 199.5 Hz |
| 006 | 82.5 Hz | 023 | 146.2 Hz | 040 | 203.5 Hz |
| 007 | 85.4 Hz | 024 | 151.4 Hz | 041 | 206.5 Hz |
| 008 | 88.5 Hz | 025 | 156.7 Hz | 042 | 210.7 Hz |
| 009 | 91.5 Hz | 026 | 159.8 Hz | 043 | 218.1 Hz |
| 010 | 94.8 Hz | 027 | 162.2 Hz | 044 | 225.7 Hz |
| 011 | 97.4 Hz | 028 | 165.5 Hz | 045 | 229.1 Hz |
| 012 | 100.0 Hz | 029 | 167.9 Hz | 046 | 233.6 Hz |
| 013 | 103.5 Hz | 030 | 171.3 Hz | 047 | 241.8 Hz |
| 014 | 107.2 Hz | 031 | 173.8 Hz | 048 | 250.3 Hz |
| 015 | 110.9 Hz | 032 | 177.3 Hz | 049 | 254.1 Hz |
| 016 | 114.8 Hz | 033 | 179.9 Hz | — | — |

---

### 6.3 EX Menu Commands — Full Parameter Table

The `EX` command reads and writes every item in the FT-891's internal menu system.

**Syntax:**
- Read: `EX0506;` (menu number only)
- Set: `EX05063;` (menu number + value)
- Response: `EX0506P;` where P is the current value

| Menu # | Function | Values |
|--------|----------|--------|
| **01 — AGC** | | |
| 0101 | AGC fast delay | 0020–4000 ms (20 ms steps) |
| 0102 | AGC mid delay | 0020–4000 ms (20 ms steps) |
| 0103 | AGC slow delay | 0020–4000 ms (20 ms steps) |
| **02 — Display** | | |
| 0201 | LCD contrast | 01–15 |
| 0202 | Dimmer backlight | 01–15 |
| 0203 | Dimmer LCD | 01–15 |
| 0204 | Dimmer TX/BUSY | 01–15 |
| 0205 | Peak hold | 0=OFF, 1=0.5s, 2=1.0s, 3=2.0s |
| 0206 | ZIN LED | 0=DISABLE, 1=ENABLE |
| 0207 | Pop-up menu | 0=UPPER, 1=LOWER |
| **03 — DVS (Digital Voice Storage)** | | |
| 0301 | DVS RX out level | 000–100 |
| 0302 | DVS TX out level | 000–100 |
| **04 — CW Settings** | | |
| 0401 | Keyer type | 0=OFF, 1=BUG, 2=ELEKEY-A, 3=ELEKEY-B, 4=ELEKEY-Y, 5=ACS |
| 0402 | Keyer dot/dash | 0=NORMAL, 1=REVERSE |
| 0403 | CW weight | 25–45 (represents 2.5–4.5) |
| 0404 | Beacon interval | 000–690 sec (000=OFF) |
| 0405 | Number style | 0=1290, 1=AUNO, 2=AUNT, 3=A2NO, 4=A2NT, 5=12NO, 6=12NT |
| 0406 | Contest number | 0000–9999 |
| 0407 | CW memory 1 type | 0=TEXT, 1=MESSAGE |
| 0408 | CW memory 2 type | 0=TEXT, 1=MESSAGE |
| 0409 | CW memory 3 type | 0=TEXT, 1=MESSAGE |
| 0410 | CW memory 4 type | 0=TEXT, 1=MESSAGE |
| 0411 | CW memory 5 type | 0=TEXT, 1=MESSAGE |
| **05 — Operational** | | |
| 0501 | NB width | 0=1ms, 1=3ms, 2=10ms |
| 0502 | NB rejection | 0=10dB, 1=30dB, 2=50dB |
| 0503 | NB level | 00–10 |
| 0504 | Beep level | 000–100 |
| 0505 | RF/SQL VR | 0=RF, 1=SQL |
| 0506 | **CAT rate** | 0=4800, 1=9600, 2=19200, 3=38400 bps |
| 0507 | CAT timeout | 0=10ms, 1=100ms, 2=1000ms, 3=3000ms |
| 0508 | CAT RTS | 0=DISABLE, 1=ENABLE |
| 0509 | Memory group | 0=DISABLE, 1=ENABLE |
| 0510 | FM setting | 0=DISABLE, 1=ENABLE |
| 0511 | REC setting | 0=DISABLE, 1=ENABLE |
| 0512 | ATAS setting | 0=DISABLE, 1=ENABLE |
| 0513 | Quick split freq | -20 to +20 kHz |
| 0514 | TX timeout (TOT) | 00–30 min (00=OFF) |
| 0515 | Mic scan | 0=DISABLE, 1=ENABLE |
| 0516 | Mic scan resume | 0=PAUSE, 1=TIME |
| 0517 | Reference freq adj | -25 to +25 |
| 0518 | CLAR select | 0=RX, 1=TX, 2=TRX |
| 0519 | APO | 0=OFF, 1=1h, 2=2h, 3=4h, 4=6h, 5=8h, 6=10h, 7=12h |
| 0520 | Fan control | 0=NORMAL, 1=CONTEST |
| **06 — AM Mode** | | |
| 0601 | AM low cut freq | 00=OFF, 01–19 (100–1000 Hz, 50 Hz steps) |
| 0602 | AM low cut slope | 0=6dB/oct, 1=18dB/oct |
| 0603 | AM high cut freq | 00=OFF, 01–67 (700–4000 Hz, 50 Hz steps) |
| 0604 | AM high cut slope | 0=6dB/oct, 1=18dB/oct |
| 0605 | AM mic select | 0=MIC, 1=REAR |
| 0606 | AM out level | 000–100 |
| 0607 | AM PTT select | 0=DAKY, 1=RTS, 2=DTR |
| **07 — CW Mode** | | |
| 0701 | CW low cut freq | 00=OFF, 01–19 (100–1000 Hz, 50 Hz steps) |
| 0702 | CW low cut slope | 0=6dB/oct, 1=18dB/oct |
| 0703 | CW high cut freq | 00=OFF, 01–67 (700–4000 Hz, 50 Hz steps) |
| 0704 | CW high cut slope | 0=6dB/oct, 1=18dB/oct |
| 0705 | CW out level | 000–100 |
| 0706 | CW auto mode | 0=OFF, 1=50M, 2=ON |
| 0707 | CW BFO | 0=USB, 1=LSB, 2=AUTO |
| 0708 | CW break-in type | 0=SEMI, 1=FULL |
| 0709 | CW break-in delay | 0030–3000 ms (10 ms steps) |
| 0710 | CW wave shape | 1=2ms, 2=4ms |
| 0711 | CW freq display | 0=FREQ, 1=PITCH |
| 0712 | PC keying | 0=OFF, 1=DAKY, 2=RTS, 3=DTR |
| 0713 | QSK delay time | 0=15ms, 1=20ms, 2=25ms, 3=30ms |
| **08 — DATA / Digital Mode** | | |
| 0801 | **Data mode** | 0=PSK, 1=OTHERS |
| 0802 | PSK tone | 0=1000Hz, 1=1500Hz, 2=2000Hz |
| 0803 | **Other disp offset** | -3000 to +3000 Hz (default: +1500 for FT8) |
| 0804 | **Other shift offset** | -3000 to +3000 Hz (default: +1500 for FT8) |
| 0805 | Data low cut freq | 00=OFF, 01–19 |
| 0806 | Data low cut slope | 0=6dB/oct, 1=18dB/oct |
| 0807 | Data high cut freq | 00=OFF, 01–67 |
| 0808 | Data high cut slope | 0=6dB/oct, 1=18dB/oct |
| 0809 | **Data in select** | 0=MIC, **1=REAR** (required for DigiRig) |
| 0810 | Data PTT select | 0=DAKY, 1=RTS, 2=DTR |
| 0811 | Data out level | 000–100 |
| 0812 | **Data BFO** | 0=USB, 1=LSB |
| **09 — FM Mode** | | |
| 0901 | FM mic select | 0=MIC, 1=REAR |
| 0902 | FM out level | 000–100 |
| 0903 | FM PTT select | 0=DAKY, 1=RTS, 2=DTR |
| 0904 | FM mic scan | 0=DISABLE, 1=ENABLE |
| 0905 | FM mic scan resume | 0=PAUSE, 1=TIME |
| **10 — RTTY Mode** | | |
| 1001 | RTTY lcut freq | 00=OFF, 01–19 |
| 1002 | RTTY lcut slope | 0=6dB/oct, 1=18dB/oct |
| 1003 | RTTY hcut freq | 00=OFF, 01–67 |
| 1004 | RTTY hcut slope | 0=6dB/oct, 1=18dB/oct |
| 1005 | RTTY out level | 000–100 |
| 1006 | RTTY shift port | 0=SHIFT, 1=DTR, 2=RTS |
| 1007 | RTTY polarity-R | 0=NOR, 1=REV |
| 1008 | RTTY polarity-T | 0=NOR, 1=REV |
| 1009 | RTTY PTT select | 0=DAKY, 1=RTS, 2=DTR |
| 1010 | RTTY mic select | 0=MIC, 1=REAR |
| 1011 | RTTY BFO | 0=LSB, 1=USB |
| **11 — SSB Mode** | | |
| 1101 | SSB lcut freq | 00=OFF, 01–19 |
| 1102 | SSB lcut slope | 0=6dB/oct, 1=18dB/oct |
| 1103 | SSB hcut freq | 00=OFF, 01–67 |
| 1104 | SSB hcut slope | 0=6dB/oct, 1=18dB/oct |
| 1105 | SSB mic select | 0=MIC, 1=REAR |
| 1106 | SSB out level | 000–100 |
| 1107 | SSB PTT select | 0=DAKY, 1=RTS, 2=DTR |
| 1108 | SSB TX BPF | 0=100-2900Hz, 1=100-3000Hz, 2=200-2800Hz, 3=200-3000Hz, 4=300-3000Hz |
| **12 — TX General** | | |
| 1201 | ALC mode | 0=AUTO, 1=MANUAL |
| 1202 | ALC slow | 00–20 |
| 1203 | ALC fast | 00–20 |
| 1204 | ALC manual | 000–100 |
| **13 — RX General** | | |
| 1301 | NB gain | 000–100 |
| 1302 | NB ATT | 0=OFF, 1=ON |
| **14 — Scope** | | |
| 1401 | Band scope mode | 0=CENTER, 1=FIX |
| 1402 | Band scope span | 0=±5kHz, 1=±10kHz, 2=±20kHz, 3=±50kHz, 4=±100kHz |
| 1403 | Band scope average | 0=OFF, 1=2, 2=4, 3=8 |
| **15 — EQ** | | |
| 1501 | EQ 1 freq | 0–11 |
| 1502 | EQ 1 level | 000–100 |
| 1503 | EQ 2 freq | 0–11 |
| 1504 | EQ 2 level | 000–100 |
| 1505 | EQ 3 freq | 0–11 |
| 1506 | EQ 3 level | 000–100 |
| 1507 | P-EQ 1 freq | 0–11 |
| 1508 | P-EQ 1 level | 000–100 |
| 1509 | P-EQ 2 freq | 0–11 |
| 1510 | P-EQ 2 level | 000–100 |
| 1511 | P-EQ 3 freq | 0–11 |
| 1512 | P-EQ 3 level | 000–100 |
| **16 — Misc** | | |
| 1601 | Main step | 0–15 (step size options) |
| 1602 | DTMF mode | 0=MANUAL, 1=AUTO |
| 1603 | DTMF delay | 50–750 ms |
| 1604 | DTMF speed | 0=50ms, 1=100ms |
| 1605 | MAIN dial step | 0–3 |
| 1606 | PANEL main dial | 0=NORM, 1=FAST |
| 1607 | PANEL other | 0=NORM, 1=FAST |
| 1608 | PANEL other step | 0–15 |
| 1609 | PANEL MF dial | 0–3 |
| 1610 | PANEL MF step | 0–15 |
| **17 — Network (not applicable for this hardware)** | | |
| **18 — Power** | | |
| 1801 | Power on mode | 0=LAST, 1=FIXED |
| 1802 | Band change | 0=BAND BUTTON, 1=RADIO |
| 1803 | Tuning indicator | 0=DISABLE, 1=ENABLE |

---

## 7. DigiRig DR-891 Notes

The DigiRig DR-891 is a purpose-built interface for the FT-891 that connects via 6-pin mini-DIN on the radio side and USB on the computer side.

**What it provides:**
- Virtual COM port for CAT commands → mapped to `/dev/ttyUSB0` (or similar)
- USB audio codec for TX/RX audio → appears as a sound card in `aplay -l` / `arecord -l`

**Key settings required on the FT-891 for DigiRig:**

| Menu | Setting | Reason |
|------|---------|--------|
| 08-09 DATA IN SELECT | REAR (value 1) | Audio must come from DigiRig rear connector |
| 08-10 DATA PTT SELECT | DAKY or RTS | PTT routing — use DAKY if using CAT PTT |
| 08-12 DATA BFO | USB | Required for proper sideband on data modes |
| 05-08 CAT RTS | DISABLE | DigiRig handles flow control differently |

**PTT routing options with DigiRig + rigctld:**

| Method | rigctld flag | Notes |
|--------|-------------|-------|
| CAT command (recommended) | `-P RIG` | Most reliable; no extra wiring |
| RTS line | `-P RTS -p /dev/ttyUSB0` | Alternative if CAT PTT causes issues |
| DTR line | `-P DTR -p /dev/ttyUSB0` | Alternative |

**Finding audio device names:**
```bash
aplay -l          # list playback devices
arecord -l        # list capture devices
# Look for the DigiRig entry, e.g. "USB PnP Sound Device"
```

---

## 8. Python Code Examples

### Complete Rig Controller Class

```python
import socket
import threading
import time

class FT891Controller:
    """
    rigctld client for Yaesu FT-891 via DigiRig on Raspberry Pi.
    Assumes rigctld is running on localhost:4532.
    """

    def __init__(self, host='localhost', port=4532):
        self.host = host
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect((self.host, self.port))

    def _cmd(self, cmd):
        with self.lock:
            self.sock.sendall((cmd + '\n').encode())
            response = b''
            while True:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    if b'RPRT' in response:
                        break
                except socket.timeout:
                    break
            return response.decode().strip()

    # --- Frequency ---
    def get_freq(self):
        r = self._cmd('f')
        return int(r.split('\n')[0])

    def set_freq(self, hz):
        return self._cmd(f'F {int(hz)}')

    # --- Mode ---
    def get_mode(self):
        r = self._cmd('m')
        lines = r.split('\n')
        return lines[0], int(lines[1]) if len(lines) > 1 else 0

    def set_mode(self, mode, passband=0):
        return self._cmd(f'M {mode} {passband}')

    # --- PTT ---
    def ptt_on(self):
        return self._cmd('T 1')

    def ptt_off(self):
        return self._cmd('T 0')

    def get_ptt(self):
        r = self._cmd('t')
        return int(r.split('\n')[0])

    # --- Levels ---
    def get_smeter(self):
        """Returns S-meter in dB relative to S9 (negative = below S9)."""
        r = self._cmd('l STRENGTH')
        return float(r.split('\n')[0])

    def get_swr(self):
        r = self._cmd('l SWR')
        return float(r.split('\n')[0])

    def get_alc(self):
        r = self._cmd('l ALC')
        return float(r.split('\n')[0])

    def set_power(self, level_0_to_1):
        """Set TX power 0.0 (min) to 1.0 (max)."""
        return self._cmd(f'L RFPOWER {level_0_to_1:.2f}')

    def get_power_watts(self):
        r = self._cmd('l RFPOWER_METER_WATTS')
        return float(r.split('\n')[0])

    # --- Functions ---
    def set_tuner(self, on=True):
        return self._cmd(f'U TUNER {"1" if on else "0"}')

    def start_atu_tune(self):
        """Trigger ATU tune cycle."""
        return self.set_tuner(True)

    def set_nr(self, on=True):
        return self._cmd(f'U NR {"1" if on else "0"}')

    def set_nb(self, on=True):
        return self._cmd(f'U NB {"1" if on else "0"}')

    # --- Raw CAT pass-through ---
    def raw_cat(self, cat_command, timeout_ms=500):
        """Send a raw FT-891 CAT command via rigctld pass-through."""
        return self._cmd(f'w {cat_command} {timeout_ms}')

    # --- High-level helpers ---
    def setup_for_ft8(self, freq_hz=14074000):
        """Configure radio for FT8 operation."""
        self.set_freq(freq_hz)
        self.set_mode('PKTUSB', 3000)
        # Set DATA IN to REAR (required for DigiRig)
        self.raw_cat('EX08091;')
        # Set DATA BFO to USB
        self.raw_cat('EX08120;')
        # Set Data mode to OTHERS
        self.raw_cat('EX08011;')

    def setup_for_cw(self, freq_hz, wpm=20):
        self.set_freq(freq_hz)
        self.set_mode('CW', 500)
        self._cmd(f'L KEYSPD {wpm}')

    def close(self):
        if self.sock:
            self.sock.close()


# Usage
if __name__ == '__main__':
    rig = FT891Controller()

    print(f"Frequency: {rig.get_freq()} Hz")
    mode, bw = rig.get_mode()
    print(f"Mode: {mode}, Passband: {bw} Hz")
    print(f"S-meter: {rig.get_smeter()} dB (relative to S9)")

    rig.setup_for_ft8(14074000)
    print("Configured for FT8 on 14.074 MHz")

    rig.close()
```

### Poll S-meter and TX Power

```python
import time

rig = FT891Controller()

print("Monitoring S-meter (Ctrl+C to stop)...")
try:
    while True:
        strength = rig.get_smeter()
        ptt = rig.get_ptt()
        if ptt:
            watts = rig.get_power_watts()
            swr = rig.get_swr()
            print(f"TX: {watts:.1f}W | SWR: {swr:.2f}")
        else:
            s9_offset = strength + 73  # rough S-unit calculation
            print(f"RX S-meter: {strength:+.0f} dBd")
        time.sleep(0.5)
except KeyboardInterrupt:
    rig.close()
```

---

## 9. Quick Answer Reference

| Developer Question | Answer & Command |
|---------------------|-----------------|
| Read current frequency | `l RFPOWER` via hamlib, or `rigctl -m 2 f` |
| Set frequency to 14.074 MHz | `rigctl -m 2 F 14074000` |
| Read current mode | `rigctl -m 2 m` |
| Switch to FT8 mode | `rigctl -m 2 M PKTUSB 3000` |
| Switch to CW narrow | `rigctl -m 2 M CW 500` |
| Read current power setting | `rigctl -m 2 l RFPOWER` (returns 0.0–1.0) |
| Read live TX power in watts | `rigctl -m 2 l RFPOWER_METER_WATTS` |
| Read SWR | `rigctl -m 2 l SWR` |
| Read ALC | `rigctl -m 2 l ALC` |
| Read S-meter | `rigctl -m 2 l STRENGTH` |
| Trigger ATU tune cycle | `rigctl -m 2 U TUNER 1` or raw: `w AC002; 500` |
| PTT on / off | `rigctl -m 2 T 1` / `T 0` |
| Can I read all menu settings? | Yes — use raw CAT `EX` commands via `w EX0506; 500` |
| Can I send raw CAT while hamlib is running? | Yes — use `w <command> <timeout_ms>` |
| Set power to 50W (of 100W max) | `rigctl -m 2 L RFPOWER 0.5` or raw: `w PC050; 500` |
| Enable noise reduction | `rigctl -m 2 U NR 1` |
| Enable full break-in CW | `rigctl -m 2 U FBKIN 1` |
| Configure for digital modes (DigiRig) | Set EX08091 (DATA IN=REAR), EX08120 (BFO=USB), EX08011 (MODE=OTHERS) |
| Check what my hamlib version supports | `rigctl -m 136 --dump-caps` |
| Swap VFO-A and VFO-B | `rigctl -m 2 G XCHG` or raw: `w SV; 500` |
| Set CW speed | `rigctl -m 2 L KEYSPD 20` |
| Lock the front panel | `rigctl -m 2 U LOCK 1` |

---

*Document generated for FT-891 / hamlib / rigctld development on Raspberry Pi with DigiRig DR-891.*  
*CAT command reference sourced from Yaesu FT-891 CAT Operation Reference Book (ENG rev 1909-C).*  
*Always run `rigctl -m 136 --dump-caps` to verify which commands your installed hamlib version supports.*
