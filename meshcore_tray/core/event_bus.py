"""Asynchronous Publish-Subscribe EventBus for MeshCore Pixoo Tray."""

import asyncio
from collections import defaultdict
import inspect
import logging
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger("meshcore_tray.event_bus")


class EventType:
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    MESSAGE_ACK = "message_ack"
    TELEMETRY_UPDATED = "telemetry_updated"
    NEIGHBOURS_UPDATED = "neighbours_updated"
    NODE_DISCOVERED = "node_discovered"
    CONNECTION_STATUS_CHANGED = "connection_status_changed"
    SETTINGS_UPDATED = "settings_updated"
    CHANNELS_UPDATED = "channels_updated"
    FAVORITES_UPDATED = "favorites_updated"
    SEARCH_REQUEST = "search_request"
    PIXOO_FRAME_READY = "pixoo_frame_ready"
    NOTIFY_USER = "notify_user"


class EventBus:
    """Thread-safe event broker linking radio drivers, UI, Pixoo, and gateways."""

    def __init__(self):
        self._subscribers: Dict[str, Set[Callable]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self, event_type: str, callback: Callable):
        """Register a callback for an event type. Callback can be sync or async."""
        self._subscribers[event_type].add(callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        """Unregister a callback."""
        if event_type in self._subscribers:
            self._subscribers[event_type].discard(callback)

    def emit(self, event_type: str, data: Any = None):
        """Publish an event synchronously or schedule async callbacks onto the event loop."""
        callbacks = list(self._subscribers.get(event_type, []))
        for cb in callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(cb(data), self._loop)
                    else:
                        asyncio.create_task(cb(data))
                else:
                    cb(data)
            except Exception as e:
                logger.error(f"Error executing callback {cb} for event {event_type}: {e}", exc_info=True)

    async def emit_async(self, event_type: str, data: Any = None):
        """Async emit awaiting all async callbacks."""
        callbacks = list(self._subscribers.get(event_type, []))
        for cb in callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as e:
                logger.error(f"Error executing async callback {cb} for event {event_type}: {e}", exc_info=True)


# Global singleton instance
bus = EventBus()
