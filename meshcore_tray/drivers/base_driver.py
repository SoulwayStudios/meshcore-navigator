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

    def connect(self):
        """Initiate connection to the radio hardware or service."""
        pass

    def reconnect(self):
        """Forces an immediate reconnection cycle."""
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

    @abstractmethod
    def join_channel(self, channel_name: str, slot: Optional[int] = None) -> Dict[str, Any]:
        """Join/configure a MeshCore group channel slot on the radio."""
        pass

    @abstractmethod
    def set_radio_params(
        self,
        frequency_mhz: float,
        bandwidth_khz: float,
        spreading_factor: int,
        coding_rate: str,
        tx_power_dbm: int,
        path_hash_mode: Optional[int] = None
    ) -> Dict[str, Any]:
        """Sets and transmits LoRa RF parameters to the hardware radio."""
        pass

    def set_path_hash_mode(self, mode: int) -> bool:
        """Sets hardware path hash mode (0: 1-Byte, 1: 2-Byte, 2: 3-Byte)."""
        return True

    def set_autoadd_contacts(self, enabled: bool) -> bool:
        """Toggles automatic addition of overheard contact adverts to radio flash."""
        return True

    def set_advert_location_policy(self, policy: int) -> bool:
        """Sets location sharing policy for adverts (0: GPS, 1: Approx, 2: None)."""
        return True

    def set_multi_acks(self, enabled: bool) -> bool:
        """Toggles multiple ACK retries on hardware."""
        return True

    def set_tuning_params(self, rx_delay_ms: int = 0, airtime_factor: int = 100) -> bool:
        """Sets radio fine tuning parameters."""
        return True

    @abstractmethod
    def resync(self):
        """Forces an explicit re-synchronization of node state, contacts, and channels."""
        pass

    @abstractmethod
    def set_node_name(self, name: str) -> Dict[str, Any]:
        """Sets and transmits node callsign/alias to the hardware radio."""
        pass

    @abstractmethod
    def send_advert(self, flood: bool = False) -> bool:
        """Broadcasts node advertisement packet (zero-hop direct or flood routed)."""
        pass
