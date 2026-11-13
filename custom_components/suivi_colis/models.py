"""Data models for Suivi de Colis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class PackageStatus(StrEnum):
    """Package tracking status."""

    UNKNOWN = "unknown"
    INFO_RECEIVED = "info_received"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    AVAILABLE_FOR_PICKUP = "available_for_pickup"
    DELIVERED = "delivered"
    DELIVERY_FAILURE = "delivery_failure"
    EXCEPTION = "exception"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"


class PackageSource(StrEnum):
    """How the package was added."""

    MANUAL = "manual"
    EMAIL = "email"


class Carrier(StrEnum):
    """Supported carriers."""

    CHRONOPOST = "chronopost"
    COLISSIMO = "colissimo"
    LAPOSTE = "laposte"
    DHL = "dhl"
    UPS = "ups"
    AMAZON = "amazon"
    CAINIAO = "cainiao"
    UNKNOWN = "unknown"


@dataclass
class TrackingEvent:
    """A single tracking event."""

    timestamp: datetime
    description: str
    location: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "location": self.location,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TrackingEvent:
        """Deserialize from dict."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            description=data["description"],
            location=data.get("location", ""),
        )


@dataclass
class Package:
    """A tracked package."""

    tracking_number: str
    carrier: str = Carrier.UNKNOWN
    friendly_name: str = ""
    status: str = PackageStatus.UNKNOWN
    carrier_code: int | None = None  # 17track carrier code the number is tracked with
    tracking_stopped: bool = False  # 17track stopped tracking (TRACKING_STOPPED)
    sub_status: str = ""  # 17track sub-status, e.g. "DeliveryFailure_NoBody"
    sub_status_descr: str = ""
    info_text: str = ""
    location: str = ""
    events: list[TrackingEvent] = field(default_factory=list)
    added_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime | None = None
    delivered_at: datetime | None = None
    estimated_delivery_from: datetime | None = None
    estimated_delivery_to: datetime | None = None
    estimated_delivery_source: str = ""
    source: str = PackageSource.MANUAL
    archived: bool = False

    @property
    def display_name(self) -> str:
        """Return friendly name or tracking number."""
        return self.friendly_name or self.tracking_number

    @property
    def last_event(self) -> TrackingEvent | None:
        """Return the most recent event."""
        return self.events[0] if self.events else None

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "friendly_name": self.friendly_name,
            "status": self.status,
            "carrier_code": self.carrier_code,
            "tracking_stopped": self.tracking_stopped,
            "sub_status": self.sub_status,
            "sub_status_descr": self.sub_status_descr,
            "info_text": self.info_text,
            "location": self.location,
            "events": [e.to_dict() for e in self.events],
            "added_at": self.added_at.isoformat(),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "estimated_delivery_from": _iso(self.estimated_delivery_from),
            "estimated_delivery_to": _iso(self.estimated_delivery_to),
            "estimated_delivery_source": self.estimated_delivery_source,
            "source": self.source,
            "archived": self.archived,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Package:
        """Deserialize from dict."""
        return cls(
            tracking_number=data["tracking_number"],
            carrier=data.get("carrier", Carrier.UNKNOWN),
            friendly_name=data.get("friendly_name", ""),
            status=data.get("status", PackageStatus.UNKNOWN),
            # Fields added in 1.1.0: defaults keep older stored packages loadable
            carrier_code=data.get("carrier_code"),
            tracking_stopped=data.get("tracking_stopped", False),
            sub_status=data.get("sub_status", ""),
            sub_status_descr=data.get("sub_status_descr", ""),
            info_text=data.get("info_text", ""),
            location=data.get("location", ""),
            events=[TrackingEvent.from_dict(e) for e in data.get("events", [])],
            added_at=datetime.fromisoformat(data["added_at"]) if data.get("added_at") else datetime.now(),
            last_updated=datetime.fromisoformat(data["last_updated"]) if data.get("last_updated") else None,
            delivered_at=datetime.fromisoformat(data["delivered_at"]) if data.get("delivered_at") else None,
            estimated_delivery_from=_from_iso(data.get("estimated_delivery_from")),
            estimated_delivery_to=_from_iso(data.get("estimated_delivery_to")),
            estimated_delivery_source=data.get("estimated_delivery_source", ""),
            source=data.get("source", PackageSource.MANUAL),
            archived=data.get("archived", False),
        )
