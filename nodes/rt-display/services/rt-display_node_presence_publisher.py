#!/usr/bin/env python3
"""
rt-display_node_presence_publisher.py

Node-local shim to satisfy systemd ExecStart allow-list while using the
common implementation at:
  /opt/rollingthunder/nodes/common/services/node_presence_publisher.py
"""
import runpy

def main() -> None:
  runpy.run_path(
    "/opt/rollingthunder/nodes/common/services/node_presence_publisher.py",
    run_name="__main__",
  )

if __name__ == "__main__":
  main()
