# Theo Bauer · ChargeRelay
"""
models.py – Pydantic request/response models for the REST API.

All input validation lives here so routers stay thin and readable.
"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

DELIVERY_TYPES = {"mail", "ocpp"}
MAIL_INTERVALS = {"daily", "weekly", "monthly", "yearly"}


# ---------------------------------------------------------------------------
# Vehicle models
# ---------------------------------------------------------------------------

class VehicleCreate(BaseModel):
    """Payload for creating a new vehicle."""
    name: str
    id_tag: Optional[str] = None
    image_data: Optional[str] = None   # base64 data URL


class VehicleUpdate(BaseModel):
    """Payload for replacing an existing vehicle's data."""
    name: str
    id_tag: Optional[str] = None
    image_data: Optional[str] = None


class SessionVehicleUpdate(BaseModel):
    """Payload for (re-)assigning a vehicle to a session."""
    vehicle_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Report models
# ---------------------------------------------------------------------------

class DeliveryIn(BaseModel):
    """A single delivery configuration attached to a report."""
    type: str
    # Mail-specific
    email: Optional[str] = None
    interval: Optional[str] = None     # daily | weekly | monthly | yearly
    # OCPP relay-specific
    address: Optional[str] = None
    port: Optional[int] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Reject delivery types that are not supported."""
        if v not in DELIVERY_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(DELIVERY_TYPES))}")
        return v

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, v: Optional[str]) -> Optional[str]:
        """Reject interval strings that are not in the allowed set."""
        if v is not None and v not in MAIL_INTERVALS:
            raise ValueError(f"interval must be one of: {', '.join(sorted(MAIL_INTERVALS))}")
        return v


class FirmwareUpdateCreate(BaseModel):
    """Payload for starting a firmware update on one station."""
    charge_point_id: str = Field(min_length=1, max_length=100)
    file_id: int


COMMANDS = {"GetConfiguration", "ChangeConfiguration", "Reset", "TriggerMessage"}
RESET_TYPES = {"Soft", "Hard"}
# OCPP 1.6 MessageTrigger values.
TRIGGER_MESSAGES = {
    "BootNotification", "DiagnosticsStatusNotification", "FirmwareStatusNotification",
    "Heartbeat", "MeterValues", "StatusNotification",
}


class StationCommandCreate(BaseModel):
    """Payload for queueing a command to a station; params depend on the command."""
    command: str
    key: Optional[str] = Field(default=None, min_length=1, max_length=50)
    value: Optional[str] = Field(default=None, max_length=500)
    type: Optional[str] = None
    message: Optional[str] = None
    connector_id: Optional[int] = Field(default=None, ge=0, le=1000)

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        """Reject commands that are not supported."""
        if v not in COMMANDS:
            raise ValueError(f"command must be one of: {', '.join(sorted(COMMANDS))}")
        return v

    def params(self) -> dict:
        """Return only the parameters that belong to the command, or raise ValueError."""
        if self.command == "ChangeConfiguration":
            # OCPP caps keys at 50 characters and the ocpp library refuses to send
            # longer ones.  Such vendor keys can be read but not changed over OCPP.
            if self.key is None or self.value is None:
                raise ValueError("ChangeConfiguration needs 'key' and 'value'")
            return {"key": self.key, "value": self.value}
        if self.command == "Reset":
            if self.type not in RESET_TYPES:
                raise ValueError(f"type must be one of: {', '.join(sorted(RESET_TYPES))}")
            return {"type": self.type}
        if self.command == "TriggerMessage":
            if self.message not in TRIGGER_MESSAGES:
                raise ValueError(f"message must be one of: {', '.join(sorted(TRIGGER_MESSAGES))}")
            params: dict = {"message": self.message}
            if self.connector_id is not None:
                params["connector_id"] = self.connector_id
            return params
        return {}


class ReportCreate(BaseModel):
    """Payload for creating a new report."""
    name: str
    vehicle_ids: list[int]
    deliveries: list[DeliveryIn]


class ReportUpdate(BaseModel):
    """Payload for replacing an existing report's configuration."""
    name: str
    vehicle_ids: list[int]
    deliveries: list[DeliveryIn]
