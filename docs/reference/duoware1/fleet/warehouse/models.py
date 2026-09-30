"""
DUO-WARE warehouse fleet: data models.

Units: metres, seconds, battery percent (0-100). Robot IDs are strings such as "R01";
checkpoint IDs are the NFC/RFID tag IDs placed in the warehouse ("C07", "RACK_A12").
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class RobotState(str, Enum):
    IDLE = "IDLE"
    MOVING = "MOVING"               # travelling without a parcel (to pickup, dock or charger)
    WAITING = "WAITING"             # holding position: next checkpoint not granted yet
    PICKING = "PICKING"
    DELIVERING = "DELIVERING"
    CARE_MODE = "CARE_MODE"         # delivering a fragile parcel with a gentle motion profile
    CHARGING = "CHARGING"
    RESERVED = "RESERVED"           # standby robot, out of normal rotation
    POSSIBLE_FAULT = "POSSIBLE_FAULT"
    FAULT = "FAULT"                 # confirmed; recovery done, waiting for inspection
    OFFLINE = "OFFLINE"
    MAINTENANCE = "MAINTENANCE"
    PAUSED = "PAUSED"


# States in which the robot is out of the normal work rotation until a human acts.
STOPPED_STATES = {RobotState.FAULT, RobotState.OFFLINE, RobotState.MAINTENANCE}


class PoolRole(str, Enum):
    NORMAL = "NORMAL"
    PEAK_RESERVE = "PEAK_RESERVE"
    EMERGENCY_RESERVE = "EMERGENCY_RESERVE"


class FleetMode(str, Enum):
    NORMAL = "NORMAL"
    BUSY = "BUSY"
    OVERLOAD = "OVERLOAD"
    RECOVERY = "RECOVERY"
    HUMAN_ASSISTANCE = "HUMAN_ASSISTANCE"


class TaskPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"           # pre-reserved for a busy robot predicted to free up first
    ASSIGNED = "ASSIGNED"           # robot travelling to pickup
    CARRYING = "CARRYING"           # parcel on board
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    PAUSED = "PAUSED"
    NEEDS_HUMAN = "NEEDS_HUMAN"     # e.g. parcel is on board a failed robot
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class CareLevel(str, Enum):
    NORMAL = "NORMAL"
    FRAGILE = "FRAGILE"
    HIGHLY_FRAGILE = "HIGHLY_FRAGILE"


class Goal(str, Enum):
    PICKUP = "PICKUP"
    DROP = "DROP"
    CHARGE = "CHARGE"
    PARK = "PARK"


@dataclass(frozen=True)
class MotionProfile:
    speed_factor: float
    max_accel_mps2: float
    braking: str
    turning: str
    safety_distance_m: float
    avoid_rough_route: bool
    confirm_at_destination: bool


CARE_PROFILES: Dict[CareLevel, MotionProfile] = {
    CareLevel.NORMAL: MotionProfile(1.0, 0.8, "normal", "normal", 0.5, False, False),
    CareLevel.FRAGILE: MotionProfile(0.6, 0.4, "gradual", "smooth", 0.8, False, False),
    CareLevel.HIGHLY_FRAGILE: MotionProfile(0.4, 0.2, "gradual, no sudden stops", "wide, no sharp turns",
                                            1.0, True, True),
}


@dataclass
class Parcel:
    """Authoritative handling record (barcode / QR / RFID / WMS). Vision never overrides it."""
    parcel_id: str
    product: str = ""
    weight_kg: float = 0.0
    care: CareLevel = CareLevel.NORMAL
    hazard: str = ""
    instructions: str = ""


@dataclass
class Task:
    task_id: str
    pickup: str
    destination: str
    priority: TaskPriority = TaskPriority.NORMAL
    parcel_id: Optional[str] = None
    deadline: Optional[float] = None
    required_capability: Optional[str] = None
    importance: float = 0.0          # extra points: production-critical / customer priority
    created_at: float = 0.0
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot: Optional[str] = None
    reserved_for: Optional[str] = None
    care: CareLevel = CareLevel.NORMAL
    attempts: int = 0
    started_at: Optional[float] = None       # first assignment; service time = completed_at - started_at
    completed_at: Optional[float] = None
    paused_from: Optional[TaskStatus] = None


@dataclass
class Robot:
    robot_id: str
    node: str                         # last confirmed checkpoint
    battery: float = 100.0
    state: RobotState = RobotState.IDLE
    pool: PoolRole = PoolRole.NORMAL
    capabilities: Set[str] = field(default_factory=set)
    home: Optional[str] = None
    speed_mps: float = 1.0
    in_service: bool = True

    task_id: Optional[str] = None
    goal: Optional[Goal] = None
    goal_node: Optional[str] = None
    route: List[str] = field(default_factory=list)   # remaining nodes, excluding current
    moving_to: Optional[str] = None                   # granted next checkpoint
    move_started: float = 0.0
    prev_node: Optional[str] = None
    care: CareLevel = CareLevel.NORMAL
    dwell_until: Optional[float] = None
    waiting_for: Optional[str] = None
    waiting_since: Optional[float] = None
    paused_state: Optional[RobotState] = None

    # telemetry
    last_comm: float = 0.0
    last_checkpoint_at: float = 0.0
    obstacle_since: Optional[float] = None
    motor_fault: bool = False
    estop: bool = False
    fault_reason: str = ""
    suspect_since: Optional[float] = None
    stopped_at: Optional[float] = None

    @property
    def profile(self) -> MotionProfile:
        return CARE_PROFILES[self.care]


@dataclass
class CheckpointRead:
    robot_id: str
    checkpoint: str
    time: float
    task_id: Optional[str]
    battery: float
    state: str
    direction: Optional[str]          # "C06->C07"
    previous: Optional[str]
    next_expected: Optional[str]
    destination: Optional[str]
    expected: bool = True


@dataclass
class Alert:
    alert_id: str
    robot_id: str
    location: str
    reason: str
    created_at: float
    task_id: Optional[str] = None
    battery: float = 0.0
    blocked_route: str = ""
    system_action: str = ""
    task_reassigned_to: Optional[str] = None
    affected_robots: List[str] = field(default_factory=list)
    technician_status: str = "NOTIFIED"   # NOTIFIED -> ACKNOWLEDGED -> RESOLVED
    acknowledged_by: Optional[str] = None
    worker_options: List[str] = field(default_factory=lambda: ["Inspect", "Approve restart",
                                                               "Remove robot", "Emergency stop"])
