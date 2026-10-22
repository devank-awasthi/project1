from __future__ import annotations

from dataclasses import dataclass

from app.enums import Role
from app.models import User


@dataclass(frozen=True)
class Actor:
    """Whoever is performing an action: a logged-in user, an anonymous
    petitioner proven by token, or a background worker."""

    kind: str  # "user" | "anon" | "system"
    user: User | None = None
    anon_hash: str | None = None
    system_name: str | None = None

    @classmethod
    def system(cls, name: str) -> "Actor":
        return cls(kind="system", system_name=name)

    @property
    def is_official(self) -> bool:
        return self.user is not None and self.user.role == Role.OFFICIAL

    @property
    def is_admin(self) -> bool:
        return self.user is not None and self.user.role == Role.ADMIN

    @property
    def ledger_label(self) -> str:
        """What the audit chain records as the actor. Anonymous petitioners
        are recorded without any identifier."""
        if self.kind == "system":
            return f"system:{self.system_name}"
        if self.kind == "anon":
            return "petitioner:anonymous"
        u = self.user
        if u.role == Role.OFFICIAL:
            return f"official:{u.designation}:{u.id}"
        return f"{u.role}:{u.id}"
