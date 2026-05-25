#!/usr/bin/env python3
"""
RollingThunder Phase 8C-1B IC-2730A / Python Hamlib low-level memory API inventory.

Safe inspection only.

This helper investigates lower-level Python Hamlib objects/functions that appeared
in Phase 8C-1:

- Hamlib.channel
- Hamlib.channelArray
- Hamlib.channel_cap
- Hamlib.chan_list
- rig.mem_count()
- rig.lookup_mem_caps()
- related parser/string helper functions

This helper does NOT:
- open the radio
- call set_mem
- call set_channel
- call set_freq
- call set_mode
- call scan
- clear memories
- write memories
- switch groups
- start scanning
- program Side B
- expose PTT/transmit controls
- write Redis
- publish rt:ui:bus
- shell out to rigctl
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from typing import Any, Dict, List


DEFAULT_MODEL = 3085


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception as exc:
        return f"<repr error: {exc}>"


def safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception as exc:
        return f"<error reading: {exc}>"


def safe_signature(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))
    except Exception as exc:
        return f"<signature unavailable: {exc}>"


def safe_call(label: str, func: Any, *args: Any) -> Dict[str, Any]:
    try:
        value = func(*args)
        return {
            "label": label,
            "ok": True,
            "value": safe_repr(value),
            "type": type(value).__name__,
            "fields": object_fields(value),
        }
    except Exception as exc:
        return {
            "label": label,
            "ok": False,
            "error": str(exc),
        }


def object_fields(obj: Any, max_fields: int = 250) -> Dict[str, str]:
    fields: Dict[str, str] = {}

    try:
        names = sorted(name for name in dir(obj) if not name.startswith("_"))
    except Exception as exc:
        return {"<dir_error>": str(exc)}

    for name in names[:max_fields]:
        fields[name] = safe_repr(safe_getattr(obj, name))

    if len(names) > max_fields:
        fields["<truncated>"] = f"{len(names) - max_fields} additional fields omitted"

    return fields


def class_inventory(Hamlib: Any, class_name: str) -> Dict[str, Any]:
    cls = getattr(Hamlib, class_name, None)

    result: Dict[str, Any] = {
        "exists": cls is not None,
        "class_repr": safe_repr(cls),
        "signature": None,
        "construct_no_args": None,
    }

    if cls is None:
        return result

    result["signature"] = safe_signature(cls)

    try:
        obj = cls()
        result["construct_no_args"] = {
            "ok": True,
            "type": type(obj).__name__,
            "repr": safe_repr(obj),
            "fields": object_fields(obj),
        }
    except Exception as exc:
        result["construct_no_args"] = {
            "ok": False,
            "error": str(exc),
        }

    return result


def function_inventory(Hamlib: Any, names: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    for name in names:
        obj = getattr(Hamlib, name, None)
        result[name] = {
            "exists": obj is not None,
            "repr": safe_repr(obj),
            "signature": safe_signature(obj) if obj is not None else None,
            "callable": callable(obj),
        }

    return result


def rig_lowlevel_inventory(Hamlib: Any, model: int) -> Dict[str, Any]:
    rig = Hamlib.Rig(model)

    result: Dict[str, Any] = {
        "rig_repr": safe_repr(rig),
        "caps_model_name": safe_repr(safe_getattr(getattr(rig, "caps", None), "model_name")),
        "caps_rig_model": safe_repr(safe_getattr(getattr(rig, "caps", None), "rig_model")),
        "method_signatures": {},
        "safe_local_calls": {},
    }

    for name in [
        "mem_count",
        "lookup_mem_caps",
        "has_scan",
        "has_vfo_op",
        "get_vfo_info",
        "chan_clear",
        "get_channel",
        "set_channel",
        "get_mem",
        "set_mem",
        "scan",
    ]:
        obj = getattr(rig, name, None)
        result["method_signatures"][name] = {
            "exists": obj is not None,
            "signature": safe_signature(obj) if obj is not None else None,
            "repr": safe_repr(obj),
        }

    # These are intended to be local capability/helper calls. They do not open
    # the radio and do not issue radio commands.
    if hasattr(rig, "mem_count"):
        result["safe_local_calls"]["rig.mem_count()"] = safe_call("rig.mem_count()", rig.mem_count)

    if hasattr(rig, "lookup_mem_caps"):
        mtypes = {
            "RIG_MTYPE_MEM": getattr(Hamlib, "RIG_MTYPE_MEM", None),
            "RIG_MTYPE_MEMOPAD": getattr(Hamlib, "RIG_MTYPE_MEMOPAD", None),
        }

        for label, value in mtypes.items():
            if value is not None:
                result["safe_local_calls"][f"rig.lookup_mem_caps({label})"] = safe_call(
                    f"rig.lookup_mem_caps({label})",
                    rig.lookup_mem_caps,
                    value,
                )

        # Some Hamlib wrappers expect an integer channel/memory number instead
        # of a memory type. These are still local caps lookups, not radio I/O.
        for channel in [1, 99]:
            result["safe_local_calls"][f"rig.lookup_mem_caps({channel})"] = safe_call(
                f"rig.lookup_mem_caps({channel})",
                rig.lookup_mem_caps,
                channel,
            )

    return result


def build_inventory() -> Dict[str, Any]:
    try:
        import Hamlib  # type: ignore
    except Exception as exc:
        return {
            "source": "ic2730a_hamlib_lowlevel_inventory",
            "phase": "8C-1B",
            "updated_utc": utc_now(),
            "ok": False,
            "error": f"Python Hamlib import failed: {exc}",
        }

    model = int(getattr(Hamlib, "RIG_MODEL_IC2730", DEFAULT_MODEL))

    return {
        "source": "ic2730a_hamlib_lowlevel_inventory",
        "phase": "8C-1B",
        "updated_utc": utc_now(),
        "ok": True,
        "safety": {
            "radio_opened": False,
            "writes_performed": False,
            "memory_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
        },
        "model": {
            "hamlib_model_used": model,
            "model_name": safe_repr(getattr(Hamlib, "RIG_MODEL_IC2730", None)),
        },
        "classes": {
            "channel": class_inventory(Hamlib, "channel"),
            "Channel": class_inventory(Hamlib, "Channel"),
            "channelArray": class_inventory(Hamlib, "channelArray"),
            "channel_cap": class_inventory(Hamlib, "channel_cap"),
            "chan_list": class_inventory(Hamlib, "chan_list"),
        },
        "functions": function_inventory(
            Hamlib,
            [
                "rig_lookup_mem_caps",
                "rig_mem_count",
                "rig_get_chan_all",
                "rig_get_mem_all",
                "rig_set_chan_all",
                "rig_set_mem_all",
                "rig_parse_vfo",
                "rig_parse_scan",
                "rig_parse_mode",
                "rig_parse_rptr_shift",
                "rig_strvfo",
                "rig_strscan",
                "rig_strrmode",
                "rig_strptrshift",
            ],
        ),
        "rig_lowlevel": rig_lowlevel_inventory(Hamlib, model),
    }


def main() -> int:
    print(json.dumps(build_inventory(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())