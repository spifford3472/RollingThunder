# nodes/rt-controller/rt_controller.py
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from config_loader import load_and_resolve_app_config, ConfigError
from config_validator import validate_or_raise, ValidationError
from redis_client import resolve_redis_conn_info, connect_and_ping, RedisConnectError
from mqtt_client import resolve_mqtt_conn_info, connect_and_probe, MqttConnectError, publish_json_event
from state_publisher import publish_initial_state, StatePublishError
from heartbeat import run_redis_heartbeat
from health_publisher import publish_controller_health



def _default_app_json_path() -> Path:
    here = Path(__file__).resolve().parent
    return (here / ".." / ".." / "config" / "app.json").resolve()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="RollingThunder rt-controller bootstrap (Phase 7B)"
    )
    parser.add_argument("--config", type=Path, default=_default_app_json_path(), help="Path to config/app.json")
    parser.add_argument("--print-config", action="store_true", help="Print resolved config after the summary (human-readable)")
    parser.add_argument("--print-config-json", action="store_true", help="Print resolved config JSON only (machine-readable; no banner/summary)")

    parser.add_argument("--redis-host", default=None, help="Override Redis host")
    parser.add_argument("--redis-port", type=int, default=None, help="Override Redis port")
    parser.add_argument("--redis-db", type=int, default=None, help="Override Redis DB index")

    parser.add_argument("--mqtt-host", default=None, help="Override MQTT host")
    parser.add_argument("--mqtt-port", type=int, default=None, help="Override MQTT port")

    parser.add_argument("--node-id", default="rt-controller", help="Logical node id")

    parser.add_argument("--once", action="store_true", help="Run bootstrap once and exit (no heartbeat loop)")
    parser.add_argument("--heartbeat-sec", type=float, default=5.0, help="Redis heartbeat interval seconds")


    args = parser.parse_args(argv)

    # Status defaults for summary
    validation_status = "NOT RUN"
    redis_status = "NOT CONNECTED"
    mqtt_status = "NOT CONNECTED"
    publish_status = "NOT RUN"
    mqtt_event_status = "NOT RUN"

    # ------------------------------------------------------------
    # Load config + resolve includes
    # ------------------------------------------------------------
    try:
        cfg, includes = load_and_resolve_app_config(args.config)
    except ConfigError as e:
        print(f"CONFIG LOAD FAILED\n------------------\n{e}", file=sys.stderr)
        return 2

    # JSON-only mode should have zero side effects
    if args.print_config_json:
        print(json.dumps(cfg, indent=2, sort_keys=False))
        return 0

    # ------------------------------------------------------------
    # Phase 3: Schema validation
    # ------------------------------------------------------------
    repo_root = args.config.resolve().parent.parent
    intents_md_path = repo_root / "docs" / "INTENTS.md"

    include_maps = {
        "pages": getattr(includes, "page_id_to_file", {}),
        "panels": getattr(includes, "panel_id_to_file", {}),
    }

    try:
        report = validate_or_raise(cfg, intents_md_path=intents_md_path, include_maps=include_maps)
        validation_status = "OK"
        boot_ms = int(time.time() * 1000)
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        validation_status = "FAILED"
        return 3

    # ------------------------------------------------------------
    # Phase 4: Redis connectivity
    # ------------------------------------------------------------
    redis_info = resolve_redis_conn_info(cfg)
    if args.redis_host:
        redis_info = redis_info.__class__(**{**redis_info.__dict__, "host": args.redis_host})
    if args.redis_port is not None:
        redis_info = redis_info.__class__(**{**redis_info.__dict__, "port": int(args.redis_port)})
    if args.redis_db is not None:
        redis_info = redis_info.__class__(**{**redis_info.__dict__, "db": int(args.redis_db)})

    try:
        redis_client = connect_and_ping(redis_info)
        redis_status = f"CONNECTED ({redis_info.host}:{redis_info.port} db={redis_info.db})"
    except RedisConnectError as e:
        print(f"REDIS CONNECT FAILED\n--------------------\n{e}", file=sys.stderr)
        return 4

    # ------------------------------------------------------------
    # Phase 5: MQTT connectivity
    # ------------------------------------------------------------
    mqtt_info = resolve_mqtt_conn_info(cfg, node_id=args.node_id)
    if args.mqtt_host:
        mqtt_info = mqtt_info.__class__(**{**mqtt_info.__dict__, "host": args.mqtt_host})
    if args.mqtt_port is not None:
        mqtt_info = mqtt_info.__class__(**{**mqtt_info.__dict__, "port": int(args.mqtt_port)})

    try:
        _ = connect_and_probe(mqtt_info)
        mqtt_status = f"CONNECTED ({mqtt_info.host}:{mqtt_info.port})"
    except MqttConnectError as e:
        print(f"MQTT CONNECT FAILED\n-------------------\n{e}", file=sys.stderr)
        return 5

    # ------------------------------------------------------------
    # Phase 6: publish initial system state to Redis
    # ------------------------------------------------------------
    try:
        publish_initial_state(
            redis_client,
            cfg,
            node_id=args.node_id,
            mqtt_connected=True,
            redis_connected=True,
            boot_ms=boot_ms,
        )
        publish_status = "OK"
    except StatePublishError as e:
        print(f"STATE PUBLISH FAILED\n--------------------\n{e}", file=sys.stderr)
        return 6

    # ------------------------------------------------------------
    # Phase 7: publish MQTT "online" event
    # ------------------------------------------------------------
    state_ns = str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()
    online_topic = f"{state_ns}/events/nodes/{args.node_id}/online"
    try:
        publish_json_event(
            mqtt_info,
            online_topic,
            {
                "node_id": args.node_id,
                "boot_ms": boot_ms,
                "status": "online",
            },
            retain=True,
            qos=1,
        )
        mqtt_event_status = "OK"
    except Exception as e:
        print(f"MQTT ONLINE EVENT PUBLISH FAILED\n-------------------------------\n{e}", file=sys.stderr)
        return 7

    # ------------------------------------------------------------
    # Phase 9: publish initial system health snapshot
    # ------------------------------------------------------------
    publish_controller_health(
        redis_client,
        cfg,
        node_id=args.node_id,
        boot_ms=boot_ms,
        mqtt_ok=("CONNECTED" in mqtt_status),
    )


    # ------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------
    print("RollingThunder Controller Bootstrap")
    print("----------------------------------")

    pages = cfg.get("pages") if isinstance(cfg.get("pages"), list) else []
    panels = cfg.get("panels") if isinstance(cfg.get("panels"), list) else []
    services = cfg.get("services")
    services_count = len(services) if isinstance(services, dict) else 0

    print(f"Loaded: {args.config}")
    if includes.pages_files:
        print(f"Pages:  {len(pages)} (from {len(includes.pages_files)} files)")
    else:
        print(f"Pages:  {len(pages)}")

    if includes.panels_files:
        print(f"Panels: {len(panels)} (from {len(includes.panels_files)} files)")
    else:
        print(f"Panels: {len(panels)}")

    print(f"Services: {services_count}")
    print(f"Validation: {validation_status}")

    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"- {w}")

    print(f"Redis: {redis_status}")
    print(f"MQTT: {mqtt_status}")
    print(f"State Publish: {publish_status}")

    if args.print_config:
        print("\n--- RESOLVED CONFIG (JSON) ---")
        print(json.dumps(cfg, indent=2, sort_keys=False))

    print(f"MQTT Online Event: {mqtt_event_status}")

    if args.once:
        return 0

    # Phase 7: heartbeat loop (runs forever)
    run_redis_heartbeat(
        redis_client,
        cfg,
        node_id=args.node_id,
        interval_sec=float(args.heartbeat_sec),
        boot_ms=boot_ms,
        mqtt_ok=("CONNECTED" in mqtt_status),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
