"""
Station Telemetry & Hardware Interface Module
=============================================
Provides a hardware-ready charger telemetry and state interface.

Design Principles:
------------------
1. Source Agnostic: Consumes telemetry from a provider abstraction (Simulated or Hardware/ESP32).
   The Station Console UI does not care whether telemetry originates from simulation or real hardware.
2. Authoritative Operational Safety: Telemetry state changes synchronize with Change 27
   Operational Eligibility Gate. A FAULT / OUT_OF_SERVICE state hard-excludes the station from
   journey planning and recommendations.
3. Hardware-Ready Contract: Prepares data models for ESP32 + INA219 current/voltage sensor + relay
   actuator without requiring API or UI refactoring when physical hardware is connected.
"""

from __future__ import annotations

import enum
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from operational_safety import (
    OperationalStatus,
    HardwareState,
    OperationalPolicy,
    OperationalEligibilityResult,
    check_operational_eligibility,
    map_hardware_state_to_operational_status,
    global_feedback_manager,
    parse_datetime_safe,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TelemetrySource(str, enum.Enum):
    """Origin source of the telemetry stream."""
    SIMULATED = "SIMULATED"
    HARDWARE = "HARDWARE"


class TelemetryQuality(str, enum.Enum):
    """Quality / measurement mode classification of electrical readings."""
    MEASURED = "MEASURED"    # Sourced from real physical current/voltage sensor (e.g. INA219 / INA226)
    NOMINAL = "NOMINAL"      # Nominal/estimated electrical values when hardware runs without physical sensor
    SIMULATED = "SIMULATED"  # Sourced from software simulation/test environment


class StationOperationalState(str, enum.Enum):
    """
    Console & hardware operational states.
    Directly aligns with HardwareState and OperationalStatus.
    """
    AVAILABLE = "AVAILABLE"
    CHARGING = "CHARGING"
    COMPLETE = "COMPLETE"
    FAULTED = "FAULTED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    MAINTENANCE = "MAINTENANCE"


# ---------------------------------------------------------------------------
# Telemetry Data Record Contract
# ---------------------------------------------------------------------------

@dataclass
class StationTelemetryRecord:
    """
    Unified hardware-ready station telemetry contract.
    Contains electrical measurements, session tracking, hardware health, and metadata.
    """
    station_id: str
    state: str = StationOperationalState.AVAILABLE.value
    voltage_v: float = 0.0
    current_a: float = 0.0
    power_w: float = 0.0
    energy_wh: float = 0.0
    connector_type: str = "CCS (Type 2)"
    rated_power_kw: float = 120.0
    fault: bool = False
    fault_code: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = TelemetrySource.SIMULATED.value
    telemetry_quality: str = TelemetryQuality.SIMULATED.value
    controller_connected: bool = True
    telemetry_connected: bool = True
    relay_closed: bool = False
    session_id: Optional[str] = None
    session_start_time: Optional[datetime] = None
    session_end_time: Optional[datetime] = None
    session_duration_seconds: float = 0.0
    station_name: str = "Grand Mercure Mysore"
    operator: str = "Zeon Charging"
    address: str = "2203 60 New Sayyaji Rao Road, Nelson Mandela Road"
    city: str = "Mysuru"
    reliability: float = 0.9328
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def power_kw(self) -> float:
        return round(self.power_w / 1000.0, 3)

    @property
    def energy_kwh(self) -> float:
        return round(self.energy_wh / 1000.0, 4)

    @property
    def timestamp_iso(self) -> str:
        if self.timestamp.tzinfo is None:
            return self.timestamp.replace(tzinfo=timezone.utc).isoformat()
        return self.timestamp.isoformat()

    @property
    def session_start_time_iso(self) -> Optional[str]:
        if self.session_start_time is not None:
            if self.session_start_time.tzinfo is None:
                return self.session_start_time.replace(tzinfo=timezone.utc).isoformat()
            return self.session_start_time.isoformat()
        return None

    @property
    def session_end_time_iso(self) -> Optional[str]:
        if self.session_end_time is not None:
            if self.session_end_time.tzinfo is None:
                return self.session_end_time.replace(tzinfo=timezone.utc).isoformat()
            return self.session_end_time.isoformat()
        return None

    @property
    def last_heartbeat_iso(self) -> str:
        if self.last_heartbeat.tzinfo is None:
            return self.last_heartbeat.replace(tzinfo=timezone.utc).isoformat()
        return self.last_heartbeat.isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "station_id": str(self.station_id),
            "state": self.state,
            "voltage_v": round(self.voltage_v, 2),
            "current_a": round(self.current_a, 2),
            "power_w": round(self.power_w, 2),
            "power_kw": self.power_kw,
            "energy_wh": round(self.energy_wh, 2),
            "energy_kwh": self.energy_kwh,
            "connector_type": self.connector_type,
            "rated_power_kw": self.rated_power_kw,
            "fault": self.fault,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp_iso,
            "source": self.source,
            "telemetry_quality": self.telemetry_quality,
            "controller_connected": self.controller_connected,
            "telemetry_connected": self.telemetry_connected,
            "relay_closed": self.relay_closed,
            "session_id": self.session_id,
            "session_start_time": self.session_start_time_iso,
            "session_end_time": self.session_end_time_iso,
            "session_duration_seconds": round(self.session_duration_seconds, 1),
            "station_name": self.station_name,
            "operator": self.operator,
            "address": self.address,
            "city": self.city,
            "reliability": round(self.reliability, 4),
            "last_heartbeat": self.last_heartbeat_iso,
        }


# ---------------------------------------------------------------------------
# Provider Abstraction
# ---------------------------------------------------------------------------

class BaseStationTelemetryProvider:
    """
    Abstract contract for station telemetry providers.
    Both Simulated and future Hardware providers implement this interface.
    """

    def get_telemetry(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        raise NotImplementedError

    def update_simulated_state(
        self,
        station_id: Union[int, str],
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> StationTelemetryRecord:
        raise NotImplementedError

    def ingest_telemetry(self, payload: Dict[str, Any]) -> StationTelemetryRecord:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Simulated Telemetry Provider
# ---------------------------------------------------------------------------

class SimulatedStationTelemetryProvider(BaseStationTelemetryProvider):
    """
    Simulates physical EV charger telemetry (voltage, current, power, energy, relay, fault).
    Used during development and testing before physical ESP32 hardware is connected.
    """

    # Default real station metadata from database (Grand Mercure Mysore, ID 7)
    DEFAULT_STATION_META = {
        "station_id": "7",
        "station_name": "Grand Mercure Mysore",
        "operator": "Zeon Charging",
        "address": "2203 60 New Sayyaji Rao Road, Nelson Mandela Road",
        "city": "Mysuru",
        "connector_type": "CCS (Type 2)",
        "rated_power_kw": 120.0,
        "reliability": 0.9328,
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._stations: Dict[str, StationTelemetryRecord] = {}
        self._initialize_default_station()

    def _initialize_default_station(self) -> None:
        def_id = self.DEFAULT_STATION_META["station_id"]
        self._stations[def_id] = StationTelemetryRecord(
            station_id=def_id,
            state=StationOperationalState.AVAILABLE.value,
            voltage_v=0.0,
            current_a=0.0,
            power_w=0.0,
            energy_wh=0.0,
            connector_type=self.DEFAULT_STATION_META["connector_type"],
            rated_power_kw=self.DEFAULT_STATION_META["rated_power_kw"],
            station_name=self.DEFAULT_STATION_META["station_name"],
            operator=self.DEFAULT_STATION_META["operator"],
            address=self.DEFAULT_STATION_META["address"],
            city=self.DEFAULT_STATION_META["city"],
            reliability=self.DEFAULT_STATION_META["reliability"],
            source=TelemetrySource.SIMULATED.value,
            telemetry_quality=TelemetryQuality.SIMULATED.value,
            controller_connected=True,
            telemetry_connected=True,
            relay_closed=False,
            fault=False,
            fault_code=None,
        )

    def _ensure_station_exists(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        c_key = str(station_id)
        if c_key not in self._stations:
            # Fallback initialized station record
            self._stations[c_key] = StationTelemetryRecord(
                station_id=c_key,
                station_name=f"Station #{c_key}",
                source=TelemetrySource.SIMULATED.value,
                telemetry_quality=TelemetryQuality.SIMULATED.value,
            )
        return self._stations[c_key]

    def get_telemetry(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        with self._lock:
            record = self._ensure_station_exists(station_id)
            now = datetime.now(timezone.utc)
            record.last_heartbeat = now
            record.timestamp = now

            # If actively charging, update session duration and accumulate energy
            if record.state == StationOperationalState.CHARGING.value and record.session_start_time:
                st = record.session_start_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (now - st).total_seconds())
                record.session_duration_seconds = elapsed

                # Realistic electrical charging simulation (CC stage: ~400V @ ~125A = 50kW)
                # Apply small deterministic variance for realism
                sec_mod = int(elapsed) % 10
                volt_delta = ((sec_mod - 5) * 0.4)
                curr_delta = (((10 - sec_mod) - 5) * 0.3)
                record.voltage_v = round(398.5 + volt_delta, 2)
                record.current_a = round(125.0 + curr_delta, 2)
                record.power_w = round(record.voltage_v * record.current_a, 2)

                # Integrate energy: Wh = Power (W) * (hours elapsed)
                # Base session Wh + live integral
                record.energy_wh = round((record.power_w * (elapsed / 3600.0)), 2)

            return record

    def update_simulated_state(
        self,
        station_id: Union[int, str],
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> StationTelemetryRecord:
        with self._lock:
            record = self._ensure_station_exists(station_id)
            params = params or {}
            now = datetime.now(timezone.utc)
            record.timestamp = now
            record.last_heartbeat = now
            record.telemetry_quality = TelemetryQuality.SIMULATED.value
            act_clean = action.strip().upper()

            if act_clean in ("START_CHARGING", "START"):
                record.state = StationOperationalState.CHARGING.value
                record.fault = False
                record.fault_code = None
                record.relay_closed = True
                record.session_id = f"SES-{uuid.uuid4().hex[:8].upper()}"
                record.session_start_time = now
                record.session_end_time = None
                record.session_duration_seconds = 0.0
                record.voltage_v = 398.5
                record.current_a = 125.0
                record.power_w = round(record.voltage_v * record.current_a, 2)
                record.energy_wh = 0.0

            elif act_clean in ("STOP_CHARGING", "STOP"):
                record.state = StationOperationalState.AVAILABLE.value
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                # Preserve session stats until new session

            elif act_clean in ("COMPLETE_SESSION", "COMPLETE"):
                record.state = StationOperationalState.COMPLETE.value
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                if record.session_start_time and not record.session_end_time:
                    record.session_end_time = now
                    st = record.session_start_time
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                    record.session_duration_seconds = max(0.0, (now - st).total_seconds())

            elif act_clean in ("SIMULATE_FAULT", "FAULT"):
                fault_code = params.get("fault_code") or "OVERCURRENT_TRIP"
                record.state = StationOperationalState.FAULTED.value
                record.fault = True
                record.fault_code = fault_code
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                if record.session_start_time and not record.session_end_time:
                    record.session_end_time = now

            elif act_clean in ("RESTORE_AVAILABLE", "RESTORE", "RESET"):
                record.state = StationOperationalState.AVAILABLE.value
                record.fault = False
                record.fault_code = None
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                record.session_id = None
                record.session_start_time = None
                record.session_end_time = None
                record.session_duration_seconds = 0.0
                record.energy_wh = 0.0

            else:
                raise ValueError(f"Unknown simulation action '{action}'")

            return record

    def ingest_telemetry(self, payload: Dict[str, Any]) -> StationTelemetryRecord:
        with self._lock:
            station_id = str(payload.get("station_id", "7"))
            record = self._ensure_station_exists(station_id)
            now = datetime.now(timezone.utc)

            record.source = payload.get("source", TelemetrySource.HARDWARE.value)
            record.state = payload.get("state", record.state)
            record.voltage_v = float(payload.get("voltage_v", record.voltage_v))
            record.current_a = float(payload.get("current_a", record.current_a))
            record.power_w = float(payload.get("power_w", record.voltage_v * record.current_a))
            record.energy_wh = float(payload.get("energy_wh", record.energy_wh))
            record.fault = bool(payload.get("fault", False))
            record.fault_code = payload.get("fault_code")
            record.controller_connected = bool(payload.get("controller_connected", True))
            record.telemetry_connected = bool(payload.get("telemetry_connected", False))
            record.relay_closed = bool(payload.get("relay_closed", False))
            record.last_heartbeat = now
            record.timestamp = parse_datetime_safe(payload.get("timestamp")) or now

            # Telemetry quality resolution
            if "telemetry_quality" in payload and payload["telemetry_quality"]:
                record.telemetry_quality = str(payload["telemetry_quality"]).upper()
            elif record.source == TelemetrySource.HARDWARE.value:
                # Without physical sensor or when telemetry_connected is False, explicitly mark NOMINAL
                if record.telemetry_connected and payload.get("telemetry_quality") == TelemetryQuality.MEASURED.value:
                    record.telemetry_quality = TelemetryQuality.MEASURED.value
                else:
                    record.telemetry_quality = TelemetryQuality.NOMINAL.value
            else:
                record.telemetry_quality = TelemetryQuality.SIMULATED.value

            if record.fault:
                record.state = StationOperationalState.FAULTED.value

            return record


# ---------------------------------------------------------------------------
# Global Singleton & Helper Functions
# ---------------------------------------------------------------------------

global_station_telemetry_provider: BaseStationTelemetryProvider = SimulatedStationTelemetryProvider()


def get_station_telemetry_with_eligibility(
    station_id: Union[int, str],
    provider: Optional[BaseStationTelemetryProvider] = None,
    policy: Optional[OperationalPolicy] = None,
) -> Dict[str, Any]:
    """
    Combines live electrical telemetry with Change 27 Authoritative Operational Safety Gate.
    Guarantees physical/telemetry state is synchronized with journey planning eligibility.
    """
    if provider is None:
        provider = global_station_telemetry_provider
    if policy is None:
        policy = OperationalPolicy()

    record = provider.get_telemetry(station_id)
    raw_dict = record.to_dict()

    # Map station state to Change 27 OperationalStatus
    if record.fault or record.state in (StationOperationalState.FAULTED.value, StationOperationalState.OUT_OF_SERVICE.value):
        op_status = OperationalStatus.OUT_OF_SERVICE
        rej_reason = f"Hardware fault detected ({record.fault_code or 'UNKNOWN_FAULT'}); relay opened."
    elif record.state == StationOperationalState.MAINTENANCE.value:
        op_status = OperationalStatus.MAINTENANCE
        rej_reason = "Station is undergoing maintenance."
    elif record.state in (StationOperationalState.AVAILABLE.value, StationOperationalState.CHARGING.value, StationOperationalState.COMPLETE.value):
        op_status = OperationalStatus.AVAILABLE
        rej_reason = None
    else:
        op_status = OperationalStatus.UNKNOWN
        rej_reason = "Operational state is unverified."

    # Run Change 27 Operational Gate
    gate_data = {
        "charger_id": record.station_id,
        "operational_status": op_status,
        "reliability": record.reliability,
        "has_active_fault": record.fault,
        "status_last_updated": record.timestamp_iso,
        "rejection_reason": rej_reason,
    }

    eligibility: OperationalEligibilityResult = check_operational_eligibility(
        gate_data,
        policy=policy,
        feedback_mgr=global_feedback_manager,
    )

    result = dict(raw_dict)
    result["operational_status"] = eligibility.operational_status.value
    result["status_confidence"] = eligibility.status_confidence
    result["trust_score"] = eligibility.trust_score
    result["eligible_for_planning"] = eligibility.eligible
    result["rejection_reason"] = eligibility.rejection_reason
    result["data_honesty_note"] = eligibility.data_honesty_note

    return result
