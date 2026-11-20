"""
In-process real-time hub shared by the WebSocket and SSE endpoints.

Every board change calls `broadcast_event`. Each event gets a sequence number and is kept
in a ring buffer, so a client that reconnects can ask for what it missed (`resume`).
`broadcast_event` may be called from the event loop or from a worker thread (the Flask app
mounted through a2wsgi runs in threads), so delivery is always scheduled on the loop.
"""
import asyncio
import json
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

BUFFER_SIZE = 1000
QUEUE_SIZE = 1000


class EventHub:
    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=BUFFER_SIZE)
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    @property
    def last_seq(self) -> int:
        return self._seq

    def publish(self, event_type: str, data: Any) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "type": event_type, "data": data, "timestamp": time.time()}
            self._buffer.append(event)

        loop = self._loop
        if loop is None or loop.is_closed():
            return event
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            loop.call_soon_threadsafe(self._deliver, event)
        return event

    def _deliver(self, event: Dict[str, Any]):
        message = json.dumps(event, default=str)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A client that cannot keep up is dropped; it will reconnect and resume.
                self._subscribers.discard(queue)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        self._subscribers.discard(queue)

    def is_subscribed(self, queue: asyncio.Queue) -> bool:
        return queue in self._subscribers

    def events_after(self, seq: int) -> Optional[List[Dict[str, Any]]]:
        """Events newer than `seq`, or None if the buffer no longer covers that gap."""
        with self._lock:
            if not self._buffer:
                return []
            if seq < self._buffer[0]["seq"] - 1:
                return None
            return [e for e in self._buffer if e["seq"] > seq]

    @property
    def client_count(self) -> int:
        return len(self._subscribers)


hub = EventHub()


def broadcast_event(event_type: str, data: Any):
    hub.publish(event_type, data)
