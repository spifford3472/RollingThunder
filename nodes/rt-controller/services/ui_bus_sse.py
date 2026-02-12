# ui_bus_sse.py
import json
import queue
import time
from dataclasses import dataclass
from typing import Dict, Set, Optional, Callable, Iterable

from flask import Response, request, stream_with_context



# Keep it tight: only allow known safe topics for UI consumption.
ALLOWED_TOPICS: Set[str] = {
    "rt/alerts/active",
    # add more later deliberately
}

@dataclass
class _Client:
    q: "queue.Queue[str]"
    last_send: float

class UiBusSseHub:
    """
    A tiny MQTT->SSE fanout hub.
    - Controller remains the authority.
    - UI gets read-only events for *allowed* topics.
    """

    def __init__(self, mqtt_subscribe: Callable[[str, Callable[[str, bytes], None]], None]):
        self._mqtt_subscribe = mqtt_subscribe
        self._clients_by_topic: Dict[str, Set[_Client]] = {}
        self._subscribed_topics: Set[str] = set()

    def _ensure_topic(self, topic: str):
        if topic in self._subscribed_topics:
            return

        def _on_msg(t: str, payload: bytes):
            # Payload is forwarded as JSON if possible, else wrapped.
            try:
                s = payload.decode("utf-8", errors="replace")
            except Exception:
                s = str(payload)

            # Bound message size to stay sane.
            if len(s) > 16_000:
                s = s[:16_000]

            try:
                obj = json.loads(s)
            except Exception:
                obj = {"topic": t, "raw": s}

            msg = json.dumps(obj, separators=(",", ":"))

            clients = self._clients_by_topic.get(t, set()).copy()
            now = time.time()
            for c in clients:
                # crude per-client rate limit: max 10 msgs/sec
                if now - c.last_send < 0.10:
                    continue
                c.last_send = now
                try:
                    c.q.put_nowait(msg)
                except queue.Full:
                    # drop; UI will still have fallback polling
                    pass

        # subscribe once per topic
        self._mqtt_subscribe(topic, _on_msg)
        self._subscribed_topics.add(topic)

    def sse_stream(self, topic: str) -> Response:
        if topic not in ALLOWED_TOPICS:
            return Response("topic_not_allowed", status=400, mimetype="text/plain")

        self._ensure_topic(topic)

        q: "queue.Queue[str]" = queue.Queue(maxsize=50)
        client = _Client(q=q, last_send=0.0)
        self._clients_by_topic.setdefault(topic, set()).add(client)

        def gen() -> Iterable[str]:
            # Initial "hello" event (optional)
            yield f": subscribed {topic}\n\n"
            last_heartbeat = time.time()

            try:
                while True:
                    # heartbeat every 15s (SSE comment)
                    now = time.time()
                    if now - last_heartbeat >= 15:
                        yield ": hb\n\n"
                        last_heartbeat = now

                    try:
                        msg = q.get(timeout=1.0)
                    except queue.Empty:
                        continue

                    yield f"data: {msg}\n\n"
            finally:
                # cleanup
                self._clients_by_topic.get(topic, set()).discard(client)

        return Response(
            stream_with_context(gen()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Nginx: disable buffering if present
                "X-Accel-Buffering": "no",
            },
        )

