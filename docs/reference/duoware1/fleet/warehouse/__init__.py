"""
DUO-WARE warehouse fleet coordination: NFC/RFID checkpoint tracking, task queue and priority
engine, battery-aware robot selection, intersection reservation, deadlock handling, fault
detection and automatic recovery, charging management, reserve robots, Care Mode, worker
controls and an explainable decision log.
"""

from .decision_log import DecisionEvent, DecisionLog
from .layout import WarehouseMap, demo_warehouse
from .manager import FleetConfig, FleetManager
from .models import (CARE_PROFILES, Alert, CareLevel, FleetMode, Parcel, PoolRole, Robot, RobotState, Task,
                     TaskPriority, TaskStatus)
from .sim import FleetSimulator

__all__ = [
    "Alert", "CARE_PROFILES", "CareLevel", "DecisionEvent", "DecisionLog", "FleetConfig", "FleetManager",
    "FleetMode", "FleetSimulator", "Parcel", "PoolRole", "Robot", "RobotState", "Task", "TaskPriority",
    "TaskStatus", "WarehouseMap", "demo_warehouse",
]
