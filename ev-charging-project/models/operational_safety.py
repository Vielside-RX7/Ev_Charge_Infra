"""
Charger Operational Safety, Trust, and Availability Module (Change 27)
======================================================================
Provides a dedicated operational eligibility gate, trust scoring, data
freshness decay, and structured user feedback management.

Product Principle:
------------------
A charger must pass an OPERATIONAL ELIGIBILITY gate before it can participate
in journey planning or recommendations.
Reliability is historical trustworthiness; Operational Status is current eligibility.
A highly reliable charger that is currently out of service MUST be rejected.
"""

from __future__ import annotations

import enum
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OperationalStatus(str, enum.Enum):
    """
    Normalized operational states for an EV charging station.
    """
    AVAILABLE = "AVAILABLE"            # Operating normally; eligible for recommendation
    DEGRADED = "DEGRADED"              # Reduced capacity / partial outage; eligible only under explicit policy
    OUT_OF_SERVICE = "OUT_OF_SERVICE"  # Hard reject; offline, confirmed failure, or active fault
    MAINTENANCE = "MAINTENANCE"        # Hard reject; active scheduled or unscheduled service
    UNKNOWN = "UNKNOWN"                # Insufficient or unverified data; never silently assumed available


class HardwareState(str, enum.Enum):
    """
    Live hardware telemetry states (e.g. ESP32 / OCPP / MQTT interface).
    Prepared for future hardware integration.
    """
    AVAILABLE = "AVAILABLE"
    CHARGING = "CHARGING"
    FAULTED = "FAULTED"
    UNAVAILABLE = "UNAVAILABLE"
    MAINTENANCE = "MAINTENANCE"


def map_hardware_state_to_operational_status(hw_state: Union[str, HardwareState]) -> OperationalStatus:
    """
    Map raw hardware/OCPP states to normalized operational status.
    """
    state_str = str(hw_state).upper()
    if state_str in (HardwareState.AVAILABLE.value, "AVAILABLE"):
        return OperationalStatus.AVAILABLE
    elif state_str in (HardwareState.CHARGING.value, "CHARGING"):
        return OperationalStatus.AVAILABLE
    elif state_str in (HardwareState.FAULTED.value, "FAULTED"):
        return OperationalStatus.OUT_OF_SERVICE
    elif state_str in (HardwareState.UNAVAILABLE.value, "UNAVAILABLE"):
        return OperationalStatus.OUT_OF_SERVICE
    elif state_str in (HardwareState.MAINTENANCE.value, "MAINTENANCE"):
        return OperationalStatus.MAINTENANCE
    else:
        return OperationalStatus.UNKNOWN


class FeedbackResult(str, enum.Enum):
    """
    Structured outcome of a charging attempt reported by users or systems.
    """
    SUCCESSFUL_CHARGE = "SUCCESSFUL_CHARGE"
    STATION_UNAVAILABLE = "STATION_UNAVAILABLE"
    CHARGER_FAULT = "CHARGER_FAULT"
    CONNECTOR_PROBLEM = "CONNECTOR_PROBLEM"
    OTHER = "OTHER"


NEGATIVE_FEEDBACK_RESULTS = {
    FeedbackResult.STATION_UNAVAILABLE,
    FeedbackResult.CHARGER_FAULT,
    FeedbackResult.CONNECTOR_PROBLEM,
}


# ---------------------------------------------------------------------------
# Configuration & Data Models
# ---------------------------------------------------------------------------

@dataclass
class OperationalPolicy:
    """
    Configurable rules governing charger operational eligibility.
    """
    allow_degraded: bool = False
    allow_unknown: bool = False
    max_stale_hours: float = 48.0
    stale_confidence_penalty: float = 0.5
    consecutive_negative_threshold: int = 3
    negative_feedback_window_hours: float = 24.0
    min_confidence_threshold: float = 0.40
    min_trust_score_for_unknown: float = 0.85


@dataclass
class ChargingFeedbackEvent:
    """
    Individual record of a charging attempt feedback report.
    """
    charger_id: Union[int, str]
    result: FeedbackResult
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    feedback_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    vehicle_id: Optional[str] = None
    session_id: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "charger_id": str(self.charger_id),
            "result": self.result.value if isinstance(self.result, FeedbackResult) else str(self.result),
            "timestamp": self.timestamp.isoformat(),
            "vehicle_id": self.vehicle_id,
            "session_id": self.session_id,
            "notes": self.notes,
        }


@dataclass
class OperationalEligibilityResult:
    """
    Evaluation output of the Operational Safety Gate for a single station.
    """
    eligible: bool
    operational_status: OperationalStatus
    status_confidence: float
    status_last_updated: Optional[datetime]
    status_age_hours: Optional[float]
    trust_score: float
    rejection_reason: Optional[str] = None
    data_honesty_note: str = "Operational confidence based on latest available data"

    @property
    def status_last_updated_iso(self) -> Optional[str]:
        if self.status_last_updated is not None:
            return self.status_last_updated.isoformat()
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible_for_planning": self.eligible,
            "operational_status": self.operational_status.value,
            "status_confidence": round(self.status_confidence, 3),
            "status_last_updated": self.status_last_updated_iso,
            "status_age_hours": round(self.status_age_hours, 1) if self.status_age_hours is not None else None,
            "trust_score": round(self.trust_score, 4),
            "rejection_reason": self.rejection_reason,
            "data_honesty_note": self.data_honesty_note,
        }


# ---------------------------------------------------------------------------
# Feedback Storage & Management
# ---------------------------------------------------------------------------

class UserFeedbackManager:
    """
    Thread-safe storage and analysis engine for user-reported charging events.
    Does not treat a single report as permanent truth, but aggregates recent
    signals to update confidence and trigger safety exclusions.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._events: Dict[str, List[ChargingFeedbackEvent]] = {}

    def record_feedback(self, event: ChargingFeedbackEvent) -> None:
        with self._lock:
            c_key = str(event.charger_id)
            if c_key not in self._events:
                self._events[c_key] = []
            self._events[c_key].append(event)
            logger.info("Recorded feedback for charger %s: %s", c_key, event.result)

    def get_feedback_for_charger(
        self,
        charger_id: Union[int, str],
        since: Optional[datetime] = None,
    ) -> List[ChargingFeedbackEvent]:
        with self._lock:
            c_key = str(charger_id)
            events = self._events.get(c_key, [])
            if since is None:
                return list(events)
            # Ensure timezone awareness compatibility
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            return [
                e for e in events
                if (e.timestamp if e.timestamp.tzinfo is not None else e.timestamp.replace(tzinfo=timezone.utc)) >= since
            ]

    def get_recent_negative_count(
        self,
        charger_id: Union[int, str],
        window_hours: float = 24.0,
        as_of: Optional[datetime] = None,
    ) -> int:
        ref_time = as_of or datetime.now(timezone.utc)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        cutoff = ref_time - timedelta(hours=window_hours)
        events = self.get_feedback_for_charger(charger_id, since=cutoff)
        return sum(1 for e in events if e.result in NEGATIVE_FEEDBACK_RESULTS)

    def get_recent_positive_count(
        self,
        charger_id: Union[int, str],
        window_hours: float = 24.0,
        as_of: Optional[datetime] = None,
    ) -> int:
        ref_time = as_of or datetime.now(timezone.utc)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        cutoff = ref_time - timedelta(hours=window_hours)
        events = self.get_feedback_for_charger(charger_id, since=cutoff)
        return sum(1 for e in events if e.result == FeedbackResult.SUCCESSFUL_CHARGE)

    def get_feedback_summary(
        self,
        charger_id: Union[int, str],
        window_hours: float = 24.0,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            c_key = str(charger_id)
            all_events = self._events.get(c_key, [])
            recent_neg = self.get_recent_negative_count(c_key, window_hours=window_hours, as_of=as_of)
            recent_pos = self.get_recent_positive_count(c_key, window_hours=window_hours, as_of=as_of)
            return {
                "total_reports": len(all_events),
                "recent_negative_reports": recent_neg,
                "recent_positive_reports": recent_pos,
                "latest_feedback": all_events[-1].to_dict() if all_events else None,
            }

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# Global in-memory feedback manager instance
global_feedback_manager = UserFeedbackManager()


# ---------------------------------------------------------------------------
# Datetime & Parsing Helpers
# ---------------------------------------------------------------------------

def parse_datetime_safe(val: Any) -> Optional[datetime]:
    """Parse various timestamp representations into a timezone-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(val, str):
        val = val.strip()
        if not val or val.lower() in ("none", "nan", "null"):
            return None
        try:
            # Handle ISO formats
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(val, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
    return None


# ---------------------------------------------------------------------------
# Trust / Reliability Score Calculation
# ---------------------------------------------------------------------------

def compute_station_trust_score(
    historical_reliability: float,
    operational_status: OperationalStatus,
    status_confidence: float,
    resolved_fault_ratio: float = 1.0,
    feedback_mgr: Optional[UserFeedbackManager] = None,
    charger_id: Optional[Union[int, str]] = None,
    as_of: Optional[datetime] = None,
) -> float:
    """
    Computes a composite station trust score [0.0, 1.0].

    Incorporates:
      1. Baseline historical reliability (from LOOCV-trained ML model).
      2. Status confidence based on freshness and data telemetry.
      3. Resolved fault ratio penalty.
      4. Recent crowd-sourced user reports (+0.05 for verified positive,
         -0.10 to -0.30 for recent negative reports).

    Crucial separation:
      Operational eligibility is a boolean gate; trust score is ranking quality.
    """
    base_score = max(0.0, min(1.0, float(historical_reliability)))

    # Fault history adjustment: if historical faults exist with low resolution ratio
    fault_mult = max(0.70, min(1.0, float(resolved_fault_ratio)))

    # User feedback adjustment
    feedback_adj = 0.0
    if feedback_mgr is not None and charger_id is not None:
        recent_neg = feedback_mgr.get_recent_negative_count(charger_id, window_hours=24.0, as_of=as_of)
        recent_pos = feedback_mgr.get_recent_positive_count(charger_id, window_hours=24.0, as_of=as_of)
        if recent_neg > 0:
            feedback_adj -= min(0.30, recent_neg * 0.10)
        if recent_pos > 0:
            feedback_adj += min(0.05, recent_pos * 0.02)

    # Combine weighted factors:
    # 70% historical reliability + 30% operational freshness & feedback
    combined = (base_score * fault_mult * 0.70) + (status_confidence * 0.30) + feedback_adj

    if operational_status == OperationalStatus.DEGRADED:
        combined *= 0.75  # Degraded stations receive a ranking trust discount

    return round(max(0.0, min(1.0, float(combined))), 4)


# ---------------------------------------------------------------------------
# Operational Eligibility Gate & Live Telemetry Overlay
# ---------------------------------------------------------------------------

_global_telemetry_provider: Any = None


def set_global_telemetry_provider(provider: Any) -> None:
    """Register the global station telemetry provider for authoritative live state overlay."""
    global _global_telemetry_provider
    _global_telemetry_provider = provider


def get_global_telemetry_provider() -> Any:
    """Retrieve the registered global station telemetry provider."""
    return _global_telemetry_provider


def normalize_station_id(station_id: Any) -> Optional[str]:
    """Normalize station identity from numeric, float string, or string representation to clean string key."""
    if station_id is None:
        return None
    s = str(station_id).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (ValueError, TypeError):
        pass
    return s


def check_operational_eligibility(
    charger_data: Dict[str, Any],
    policy: Optional[OperationalPolicy] = None,
    feedback_mgr: Optional[UserFeedbackManager] = None,
    as_of: Optional[datetime] = None,
    telemetry_provider: Optional[Any] = None,
) -> OperationalEligibilityResult:
    """
    Authoritative Operational Eligibility Gate.

    Evaluates whether a candidate charger is currently eligible for journey planning.
    Before charger scoring:
        candidate -> operational eligibility gate -> pass (scoring) / fail (hard reject)

    Rejects candidate when strong evidence indicates:
      - Active fault
      - Active maintenance
      - Explicitly out of service or unavailable
      - Confirmed recent failures / repeated negative user reports
      - Operational status is UNKNOWN (under default policy)

    Invariant:
      High reliability, low price, short distance, or fast charging
      CANNOT override a hard operational rejection.
    """
    if policy is None:
        policy = OperationalPolicy()
    if feedback_mgr is None:
        feedback_mgr = global_feedback_manager

    ref_time = as_of or datetime.now(timezone.utc)
    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)

    raw_id = charger_data.get("charger_id") or charger_data.get("id") or charger_data.get("station_id")
    charger_id = normalize_station_id(raw_id)

    # 0. Live Telemetry / Hardware Provider Overlay
    # If an authoritative telemetry provider has a live record for this station,
    # overlay its live state, active fault flag, and rejection reason.
    effective_provider = telemetry_provider if telemetry_provider is not None else _global_telemetry_provider
    live_telemetry_rec = None
    if charger_id is not None and effective_provider is not None:
        try:
            get_live_fn = getattr(effective_provider, "get_live_telemetry_if_exists", None)
            if callable(get_live_fn):
                live_telemetry_rec = get_live_fn(charger_id)
        except Exception as exc:
            logger.debug("Live telemetry overlay lookup error for charger %s: %s", charger_id, exc)

    if live_telemetry_rec is not None:
        charger_data = dict(charger_data)
        if live_telemetry_rec.fault or live_telemetry_rec.state in (
            OperationalStatus.OUT_OF_SERVICE.value,
            "FAULTED",
            "FAULT",
        ):
            charger_data["operational_status"] = OperationalStatus.OUT_OF_SERVICE
            charger_data["has_active_fault"] = True
            charger_data["rejection_reason"] = (
                f"Hardware fault detected ({live_telemetry_rec.fault_code or 'UNKNOWN_FAULT'}); safety lockout."
            )
            if hasattr(live_telemetry_rec, "timestamp_iso"):
                charger_data["status_last_updated"] = live_telemetry_rec.timestamp_iso
        elif live_telemetry_rec.state in (OperationalStatus.MAINTENANCE.value, "MAINTENANCE"):
            charger_data["operational_status"] = OperationalStatus.MAINTENANCE
            charger_data["under_maintenance"] = True
            charger_data["rejection_reason"] = "Station is undergoing maintenance."
            if hasattr(live_telemetry_rec, "timestamp_iso"):
                charger_data["status_last_updated"] = live_telemetry_rec.timestamp_iso
        elif hasattr(live_telemetry_rec, "timestamp_iso") and "status_last_updated" not in charger_data:
            charger_data["status_last_updated"] = live_telemetry_rec.timestamp_iso

    # 1. Determine base operational status
    raw_status = charger_data.get("operational_status")
    status: Optional[OperationalStatus] = None

    if raw_status is not None:
        if isinstance(raw_status, OperationalStatus):
            status = raw_status
        else:
            s_clean = str(raw_status).strip().upper()
            try:
                status = OperationalStatus(s_clean)
            except ValueError:
                status = OperationalStatus.UNKNOWN

    # Check hardware state if provided
    hw_state = charger_data.get("hardware_state")
    if hw_state is not None:
        status = map_hardware_state_to_operational_status(hw_state)

    # Derive status from dataset fields if not explicitly provided
    if status is None:
        has_active_fault = bool(charger_data.get("has_active_fault", False))
        unresolved_faults = int(charger_data.get("num_unresolved_faults", 0))
        if "resolved" in charger_data and charger_data["resolved"] is False:
            has_active_fault = True
        num_faults = int(charger_data.get("num_faults", 0))
        num_resolved = int(charger_data.get("num_resolved_faults", 0))
        if num_faults > num_resolved and "days_since_last_fault" in charger_data:
            days = float(charger_data.get("days_since_last_fault") or 999.0)
            if days <= 1.0:  # Recent unresolved fault within 24h
                has_active_fault = True

        under_maintenance = bool(charger_data.get("under_maintenance", False)) or bool(charger_data.get("is_maintenance", False))

        if has_active_fault or unresolved_faults > 0:
            status = OperationalStatus.OUT_OF_SERVICE
        elif under_maintenance:
            status = OperationalStatus.MAINTENANCE
        else:
            # Check if there is enough metadata to infer operational availability
            # In existing dataset / mock_df, stations with known specs and no outages are AVAILABLE
            status = OperationalStatus.AVAILABLE

    # 2. Check for active faults explicitly flagged
    if bool(charger_data.get("has_active_fault", False)) or (charger_data.get("resolved") is False and "fault_type" in charger_data):
        status = OperationalStatus.OUT_OF_SERVICE

    # 3. Check for active maintenance explicitly flagged
    if bool(charger_data.get("under_maintenance", False)) or bool(charger_data.get("is_maintenance", False)):
        status = OperationalStatus.MAINTENANCE

    # 4. Check for repeated negative crowd reports
    if charger_id is not None:
        neg_count = feedback_mgr.get_recent_negative_count(
            charger_id, window_hours=policy.negative_feedback_window_hours, as_of=ref_time
        )
        if neg_count >= policy.consecutive_negative_threshold:
            status = OperationalStatus.OUT_OF_SERVICE
            reason_neg = (
                f"Temporarily excluded: {neg_count} consecutive negative user reports received "
                f"in the last {policy.negative_feedback_window_hours:.0f} hours."
            )
        else:
            reason_neg = None
    else:
        neg_count = 0
        reason_neg = None

    # 5. Determine Freshness & Confidence
    ts_val = (
        charger_data.get("status_last_updated")
        or charger_data.get("last_heartbeat")
        or charger_data.get("telemetry_timestamp")
    )
    last_updated_dt = parse_datetime_safe(ts_val)
    age_hours: Optional[float] = None
    confidence: float = 1.0

    if last_updated_dt is not None:
        delta_sec = max(0.0, (ref_time - last_updated_dt).total_seconds())
        age_hours = delta_sec / 3600.0

        if age_hours <= policy.max_stale_hours:
            # Fresh data: gentle decay from 1.0 down to 0.85 as it approaches cutoff
            confidence = max(0.85, 1.0 - (age_hours / policy.max_stale_hours) * 0.15)
        else:
            # Stale data: exponential / inverse reduction
            stale_ratio = age_hours / policy.max_stale_hours
            confidence = max(0.10, 0.85 * (1.0 / stale_ratio) * policy.stale_confidence_penalty)
    else:
        # No timestamp available
        if status == OperationalStatus.UNKNOWN:
            confidence = 0.30
        else:
            # Historical dataset baseline: confident within model parameters but not real-time confirmed
            confidence = 0.85

    # Single negative report penalty on confidence
    if neg_count > 0 and status != OperationalStatus.OUT_OF_SERVICE:
        confidence = max(0.10, confidence - (neg_count * 0.20))

    # 6. Base reliability score extraction
    hist_rel = float(charger_data.get("reliability", 0.85))
    fault_ratio = float(charger_data.get("resolved_fault_ratio", 1.0))
    trust_score = compute_station_trust_score(
        historical_reliability=hist_rel,
        operational_status=status,
        status_confidence=confidence,
        resolved_fault_ratio=fault_ratio,
        feedback_mgr=feedback_mgr,
        charger_id=charger_id,
        as_of=ref_time,
    )

    # 7. Apply Operational Eligibility Decision Policy
    eligible = False
    rejection_reason: Optional[str] = None

    if status == OperationalStatus.OUT_OF_SERVICE:
        eligible = False
        raw_rej = charger_data.get("rejection_reason")
        if reason_neg:
            rejection_reason = reason_neg
        elif raw_rej:
            rejection_reason = f"Station is OUT_OF_SERVICE: {raw_rej}" if "OUT_OF_SERVICE" not in raw_rej else raw_rej
        else:
            rejection_reason = "Station is OUT_OF_SERVICE: confirmed failure or active fault."

    elif status == OperationalStatus.MAINTENANCE:
        eligible = False
        raw_rej = charger_data.get("rejection_reason")
        if raw_rej:
            rejection_reason = f"Station is under MAINTENANCE: {raw_rej}" if "MAINTENANCE" not in raw_rej else raw_rej
        else:
            rejection_reason = "Station is under MAINTENANCE: scheduled or unscheduled service in progress."

    elif status == OperationalStatus.UNKNOWN:
        if not policy.allow_unknown:
            eligible = False
            rejection_reason = "Operational status is UNKNOWN and unverified; not eligible under safety policy."
        else:
            if trust_score >= policy.min_trust_score_for_unknown and confidence >= policy.min_confidence_threshold:
                eligible = True
                rejection_reason = None
            else:
                eligible = False
                rejection_reason = (
                    f"Operational status is UNKNOWN and trust score ({trust_score:.2f}) "
                    f"does not meet policy threshold ({policy.min_trust_score_for_unknown:.2f})."
                )

    elif status == OperationalStatus.DEGRADED:
        if not policy.allow_degraded:
            eligible = False
            rejection_reason = "Station is DEGRADED (partial port or power loss); policy disallows degraded chargers."
        else:
            eligible = True
            rejection_reason = None

    elif status == OperationalStatus.AVAILABLE:
        # Check confidence threshold for stale data
        if confidence < policy.min_confidence_threshold:
            eligible = False
            rejection_reason = (
                f"Operational confidence ({confidence:.2f}) is below minimum required "
                f"({policy.min_confidence_threshold:.2f}) due to stale telemetry data ({age_hours:.1f}h old)."
            )
        else:
            eligible = True
            rejection_reason = None

    return OperationalEligibilityResult(
        eligible=eligible,
        operational_status=status,
        status_confidence=round(confidence, 3),
        status_last_updated=last_updated_dt,
        status_age_hours=round(age_hours, 1) if age_hours is not None else None,
        trust_score=round(trust_score, 4),
        rejection_reason=rejection_reason,
    )


# ---------------------------------------------------------------------------
# Provider Abstractions (Future Hardware & Live Real-Time Boundary)
# ---------------------------------------------------------------------------

class OperationalStatusProvider:
    """
    Abstract interface for retrieving charger operational status.
    Designed to allow future live telematics (ESP32 / OCPP / MQTT / external live APIs)
    to cleanly replace or supplement historical/synthetic database status.
    """

    def get_status(
        self,
        charger_id: Union[int, str],
        charger_data: Optional[Dict[str, Any]] = None,
        policy: Optional[OperationalPolicy] = None,
        as_of: Optional[datetime] = None,
    ) -> OperationalEligibilityResult:
        raise NotImplementedError


class HistoricalDatabaseStatusProvider(OperationalStatusProvider):
    """
    Standard provider that derives operational eligibility from database records,
    historical metrics, and in-memory feedback.
    """

    def __init__(self, feedback_manager: Optional[UserFeedbackManager] = None):
        self.feedback_manager = feedback_manager or global_feedback_manager

    def get_status(
        self,
        charger_id: Union[int, str],
        charger_data: Optional[Dict[str, Any]] = None,
        policy: Optional[OperationalPolicy] = None,
        as_of: Optional[datetime] = None,
    ) -> OperationalEligibilityResult:
        data = dict(charger_data or {})
        data.setdefault("charger_id", charger_id)
        return check_operational_eligibility(
            data,
            policy=policy,
            feedback_mgr=self.feedback_manager,
            as_of=as_of,
        )


class SimulatedLiveStatusProvider(OperationalStatusProvider):
    """
    Simulated live telemetry provider allowing mock ESP32/OCPP state injection
    for testing dynamic hardware state transitions without external network calls.
    """

    def __init__(self):
        self._states: Dict[str, Dict[str, Any]] = {}

    def set_live_status(
        self,
        charger_id: Union[int, str],
        operational_status: OperationalStatus,
        last_updated: Optional[datetime] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        self._states[str(charger_id)] = {
            "operational_status": operational_status,
            "status_last_updated": last_updated or datetime.now(timezone.utc),
            "rejection_reason": rejection_reason,
        }

    def get_status(
        self,
        charger_id: Union[int, str],
        charger_data: Optional[Dict[str, Any]] = None,
        policy: Optional[OperationalPolicy] = None,
        as_of: Optional[datetime] = None,
    ) -> OperationalEligibilityResult:
        data = dict(charger_data or {})
        c_key = str(charger_id)
        if c_key in self._states:
            data.update(self._states[c_key])
        return check_operational_eligibility(data, policy=policy, as_of=as_of)
