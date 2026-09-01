"""
SQLAlchemy ORM models for the EV Charging Intelligence platform.

Tables:
    - chargers: EV charging station metadata and location
    - users: Registered EV drivers and vehicle info
    - charging_sessions: Individual charging session records
    - reviews: User reviews and NLP-derived sentiment for chargers
    - faults: Charger fault/downtime event reports
    - maintenance_logs: Scheduled and unscheduled maintenance records
"""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ── Base ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────────────

class SessionStatus(str, enum.Enum):
    success = "success"
    failed = "failed"
    interrupted = "interrupted"


class SessionSource(str, enum.Enum):
    real = "real"
    simulated = "simulated"


class FaultSource(str, enum.Enum):
    user_report = "user_report"
    system = "system"


# ── Models ──────────────────────────────────────────────────────────────

class Charger(Base):
    __tablename__ = "chargers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(255), nullable=True, comment="E.g. Open Charge Map ID")
    name = Column(String(255), nullable=False)
    operator = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    connector_type = Column(String(100), nullable=True)
    charging_power_kw = Column(Float, nullable=True)
    num_ports = Column(Integer, nullable=True)
    source = Column(String(100), nullable=False, default="open_charge_map")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    sessions = relationship("ChargingSession", back_populates="charger", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="charger", cascade="all, delete-orphan")
    faults = relationship("Fault", back_populates="charger", cascade="all, delete-orphan")
    maintenance_logs = relationship("MaintenanceLog", back_populates="charger", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Charger(id={self.id}, name='{self.name}', city='{self.city}')>"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    vehicle_model = Column(String(255), nullable=True)
    battery_capacity_kwh = Column(Float, nullable=True)
    preferred_connector_type = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    sessions = relationship("ChargingSession", back_populates="user")
    reviews = relationship("Review", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"


class ChargingSession(Base):
    __tablename__ = "charging_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(Integer, ForeignKey("chargers.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    energy_delivered_kwh = Column(Float, nullable=True)
    status = Column(Enum(SessionStatus), nullable=False)
    soc_start = Column(Float, nullable=True, comment="State of charge at session start (%)")
    soc_end = Column(Float, nullable=True, comment="State of charge at session end (%)")
    cost = Column(Float, nullable=True)
    source = Column(Enum(SessionSource), nullable=False, default=SessionSource.simulated)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    charger = relationship("Charger", back_populates="sessions")
    user = relationship("User", back_populates="sessions")

    def __repr__(self):
        return f"<ChargingSession(id={self.id}, charger_id={self.charger_id}, status='{self.status}')>"


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(Integer, ForeignKey("chargers.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rating = Column(Integer, nullable=False, comment="Rating from 1 to 5")
    review_text = Column(Text, nullable=True)
    sentiment_score = Column(Float, nullable=True, comment="Filled later by NLP model")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    charger = relationship("Charger", back_populates="reviews")
    user = relationship("User", back_populates="reviews")

    def __repr__(self):
        return f"<Review(id={self.id}, charger_id={self.charger_id}, rating={self.rating})>"


class Fault(Base):
    __tablename__ = "faults"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(Integer, ForeignKey("chargers.id"), nullable=False)
    reported_at = Column(DateTime, nullable=False)
    fault_type = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_at = Column(DateTime, nullable=True)
    source = Column(Enum(FaultSource), nullable=False, default=FaultSource.user_report)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    charger = relationship("Charger", back_populates="faults")

    def __repr__(self):
        return f"<Fault(id={self.id}, charger_id={self.charger_id}, fault_type='{self.fault_type}')>"


class MaintenanceLog(Base):
    __tablename__ = "maintenance_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(Integer, ForeignKey("chargers.id"), nullable=False)
    maintenance_date = Column(DateTime, nullable=False)
    description = Column(Text, nullable=True)
    technician = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    charger = relationship("Charger", back_populates="maintenance_logs")

    def __repr__(self):
        return f"<MaintenanceLog(id={self.id}, charger_id={self.charger_id})>"
