from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContactState(str, Enum):
    READY = "Ready"
    DUPLICATE = "Duplicate"
    INVALID = "Invalid"
    SENDING = "Sending"
    SENT = "Sent"
    FAILED = "Failed"
    STOPPED = "Stopped"


@dataclass
class Contact:
    row_number: int
    original_phone: str
    phone: str = ""
    name: str = ""
    state: ContactState = ContactState.READY
    detail: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == ContactState.READY


@dataclass(frozen=True)
class SendResult:
    contact: Contact
    state: ContactState
    detail: str
