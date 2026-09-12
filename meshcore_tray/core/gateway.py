"""Extensibility Gateway & Local JSON/REST Bridge for External Services & SDRs."""

import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
import threading
from typing import Any, Dict, Optional
from meshcore_tray.core.models import CommandPacket, MessageEnvelope
from meshcore_tray.core.event_bus import bus, EventType

logger = logging.getLogger("meshcore_tray.gateway")


class GatewayManager:
    """Manages command parsing, external scripts, and local HTTP REST server."""

    def __init__(self, config=None, storage=None, radio_driver=None):
        self.config = config
        self.storage = storage
        self.radio_driver = radio_driver
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def execute_command(self, cmd: CommandPacket) -> Dict[str, Any]:
        """Dispatches clean command packets to the radio driver or internal event bus."""
        cmd_type = cmd.command_type.upper()
        logger.info(f"Executing gateway command: {cmd_type} (Payload: {cmd.payload})")

        if cmd_type == "SEND_MSG":
            channel = cmd.payload.get("channel", "Public")
            text = cmd.payload.get("text", "")
            if self.radio_driver:
                return self.radio_driver.send_channel_message(channel, text)
            return {"status": "error", "message": "No radio driver connected"}

        elif cmd_type == "SEND_DM":
            recipient = cmd.payload.get("recipient_id") or cmd.payload.get("recipient_name")
            text = cmd.payload.get("text", "")
            if self.radio_driver:
                return self.radio_driver.send_direct_message(recipient, text)
            return {"status": "error", "message": "No radio driver connected"}

        elif cmd_type == "QUERY_TELEMETRY":
            if self.radio_driver:
                return self.radio_driver.query_telemetry(cmd.payload.get("node_id", "local"))
            return {"status": "error", "message": "No radio driver connected"}

        elif cmd_type == "QUERY_NEIGHBOURS":
            target = cmd.payload.get("target_node_id")
            if self.radio_driver:
                return self.radio_driver.query_neighbours(target)
            return {"status": "error", "message": "No radio driver connected"}

        elif cmd_type == "GET_MESSAGES":
            channel = cmd.payload.get("channel")
            limit = min(int(cmd.payload.get("limit", 50)), 200)
            if self.storage:
                msgs = self.storage.get_messages(channel=channel, limit=limit, include_dms=False)
                return {"status": "ok", "messages": [m.to_dict() for m in msgs if not m.is_direct_message]}
            return {"status": "error", "message": "No storage available"}

        elif cmd_type == "GET_TELEMETRY":
            if self.storage:
                telem = self.storage.get_latest_telemetry()
                return {"status": "ok", "telemetry": telem.to_dict() if telem else None}
            return {"status": "error", "message": "No storage available"}

        elif cmd_type == "GET_NEIGHBOURS":
            if self.storage:
                neighbours = self.storage.get_neighbours()
                return {"status": "ok", "neighbours": [n.to_dict() for n in neighbours]}
            return {"status": "error", "message": "No storage available"}

        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def start_http_bridge(self, port: int = 18680):
        """Starts local JSON REST bridge for external scripts, SDRs, and webhooks."""
        if self._server:
            return

        gateway = self

        class GatewayHTTPHandler(BaseHTTPRequestHandler):
            def _send_json(self, status_code: int, data: Dict[str, Any]):
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                # No wildcard CORS: protect against malicious websites invoking loopback APIs
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))

            def _is_origin_allowed(self) -> bool:
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                from urllib.parse import urlparse
                parsed = urlparse(origin)
                return parsed.hostname in ("127.0.0.1", "localhost", "::1")

            def _is_authenticated(self) -> bool:
                expected_token = ""
                if gateway.config and hasattr(gateway.config, "gateway") and getattr(gateway.config.gateway, "api_token", ""):
                    expected_token = gateway.config.gateway.api_token.strip()
                if not expected_token:
                    return True
                auth_hdr = self.headers.get("Authorization", "")
                if auth_hdr.startswith("Bearer "):
                    token = auth_hdr[7:].strip()
                else:
                    token = self.headers.get("X-API-Key", "").strip()
                return token == expected_token

            def do_GET(self):
                if not self._is_origin_allowed():
                    self._send_json(403, {"error": "Cross-origin requests from external web pages are forbidden"})
                    return
                if not self._is_authenticated():
                    self._send_json(401, {"error": "Unauthorized: invalid or missing API token"})
                    return

                if self.path == "/api/status":
                    self._send_json(200, {
                        "app": "MeshCore Pixoo Tray",
                        "status": "online",
                        "driver": gateway.radio_driver.__class__.__name__ if gateway.radio_driver else "None"
                    })
                elif self.path.startswith("/api/messages"):
                    res = gateway.execute_command(CommandPacket("GET_MESSAGES"))
                    self._send_json(200, res)
                elif self.path == "/api/telemetry":
                    res = gateway.execute_command(CommandPacket("GET_TELEMETRY"))
                    self._send_json(200, res)
                elif self.path == "/api/neighbours":
                    res = gateway.execute_command(CommandPacket("GET_NEIGHBOURS"))
                    self._send_json(200, res)
                elif self.path == "/api/sync_channels":
                    if gateway.radio_driver and hasattr(gateway.radio_driver, "sync_channels"):
                        res = gateway.radio_driver.sync_channels()
                        self._send_json(200, res)
                    else:
                        self._send_json(400, {"status": "error", "message": "Radio driver not available"})
                elif self.path == "/api/radio/channels":
                    if gateway.storage:
                        app_channels = [{"slot": ch.channel_id, "name": ch.name, "favorite": ch.is_favorite} for ch in gateway.storage.get_channels()]
                        self._send_json(200, {"channels": app_channels})
                    else:
                        self._send_json(400, {"status": "error", "message": "Storage not available"})
                else:
                    self._send_json(404, {"error": "Not Found"})

            def do_POST(self):
                if not self._is_origin_allowed():
                    self._send_json(403, {"error": "Cross-origin requests from external web pages are forbidden"})
                    return
                if not self._is_authenticated():
                    self._send_json(401, {"error": "Unauthorized: invalid or missing API token"})
                    return

                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length > 65536:
                        self._send_json(413, {"error": "Payload exceeds 64KB limit"})
                        return

                    body = self.rfile.read(content_length).decode("utf-8")
                    data = json.loads(body) if body else {}

                    if self.path == "/api/send":
                        cmd = CommandPacket(
                            command_type="SEND_MSG" if "channel" in data else "SEND_DM",
                            payload=data
                        )
                        res = gateway.execute_command(cmd)
                        self._send_json(200, res)
                    elif self.path == "/api/command":
                        cmd = CommandPacket.from_json(body)
                        res = gateway.execute_command(cmd)
                        self._send_json(200, res)
                    else:
                        self._send_json(404, {"error": "Endpoint not found"})
                except Exception as e:
                    logger.error(f"HTTP bridge POST error: {e}", exc_info=True)
                    self._send_json(400, {"error": str(e)})

            def log_message(self, format, *args):
                pass  # Silence standard HTTP access logs

        try:
            self._server = HTTPServer(("127.0.0.1", port), GatewayHTTPHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info(f"Local Gateway HTTP bridge listening on http://127.0.0.1:{port}")
        except Exception as e:
            logger.warning(f"Could not start local HTTP bridge on port {port}: {e}")

    def stop_http_bridge(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None
