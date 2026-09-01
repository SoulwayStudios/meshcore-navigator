"""Abstract Base Radio Driver for Extensibility (MeshCore, SDRs, Amateur Radio)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from meshcore_tray.core.models import MessageEnvelope, TelemetryEnvelope


class BaseRadioDriver(ABC):
    """Abstract interface for all hardware and virtual radio drivers."""

    @abstractmethod
    async def start(self):
        """Initialize driver and start background event listener loop."""
        pass

    @abstractmethod
    async def stop(self):
        """Disconnect and clean up resources."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Returns connection status."""
        pass

    @abstractmethod
    def send_channel_message(self, channel: str, text: str) -> Dict[str, Any]:
        """Transmit message to a public or private channel."""
        pass

    @abstractmethod
    def send_direct_message(self, recipient_id: str, text: str) -> Dict[str, Any]:
        """Transmit direct message to a specific contact node."""
        pass

    @abstractmethod
    def query_telemetry(self, node_id: str = "local") -> Dict[str, Any]:
        """Request RF telemetry (Frequency, BW, SF, CR, TX, Noise floor)."""
        pass

    @abstractmethod
    def query_neighbours(self, target_node_id: Optional[str] = None) -> Dict[str, Any]:
        """Request neighbor table with SNR/RSSI metrics."""
        pass
