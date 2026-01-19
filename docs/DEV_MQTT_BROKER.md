# DEV_MQTT_BROKER.md  
RollingThunder – Development MQTT Broker Configuration

## Purpose

This document records a **development-time MQTT broker configuration**
required to support **multi-node presence** in the RollingThunder architecture.

It exists to prevent future confusion when:
- a display node cannot connect to the controller’s MQTT broker
- presence ingestion appears to “not work”
- Redis node state remains empty despite valid publishers

This configuration is **intentional**, **documented**, and **dev-only**.

---

## Architectural Context

RollingThunder uses MQTT as an **event bus**, not a database.

For Phase 14 (multi-node presence):
- `rt-display` publishes presence heartbeats
- `rt-controller` subscribes to `rt/presence/+`
- Controller derives online/offline state and writes to Redis

This requires that **remote nodes can connect to the controller’s MQTT broker**.

By default, Mosquitto on Debian / Raspberry Pi OS listens on **localhost only**.
That is insufficient for multi-node development.

---

## Required Change (Dev / Lab Only)

The MQTT broker on `rt-controller` must listen on a LAN interface.

### Configuration File

Create the following file on the controller:

```

### Contents

# RollingThunder development broker listener
# Allows remote nodes (display, radio) to connect during dev

listener 1883 0.0.0.0

# Development convenience ONLY
# Do not use this configuration on untrusted networks
allow_anonymous true
```

## Activation

After creating or modifying the file:
```
sudo systemctl restart mosquitto
```

Verify the broker is listening on the LAN:
```
ss -ltnp | grep 1883
```
Expected output includes:
```
0.0.0.0:1883
```

## Verification (End-to-End) ##

From a remote dev machine:
```
nc -vz rt-controller 1883
```
Expected: connection succeeds.

From the controller itself:
```
mosquitto_sub -t 'rt/presence/#' -v
```

You should see JSON presence messages when a node is publishing.
---

# Security Notes (Important) #

This configuration is **NOT appropriate for production or untrusted networks.**

Specifically:

 - allow_anonymous true disables authentication

 - listener 0.0.0.0 exposes the broker to the LAN

This is acceptable **only because:**

 - RollingThunder dev environments run on trusted networks

 - MQTT is used for non-sensitive telemetry and events

 - All control actions remain gated elsewhere

Future hardening options include:
 - binding to a specific private interface
 - enabling username/password authentication
 - using TLS
 - isolating MQTT traffic on a dedicated VLAN

None of those are required for Phase 14 development.

## Why This Is Documented ##

This file exists because **invisible infrastructure changes cause the most pain.**

If multi-node presence works on one system but not another, this document should
be the first thing checked.

If this configuration changes, the change should be:
 - deliberate
 - documented
 - version-controlled (in docs)

Silent drift is considered a bug.