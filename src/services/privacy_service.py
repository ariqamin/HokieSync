from __future__ import annotations

from src.core.db import Database


class PrivacyService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def can_view_schedule(self, owner_user_id: int, requester_user_id: int) -> bool:
        if owner_user_id == requester_user_id:
            return True
        profile = self.db.get_profile(owner_user_id)
        if profile is None:
            return False
        if profile.privacy == "public":
            return True
        if profile.privacy == "friends":
            return self.db.is_friend(owner_user_id, requester_user_id)
        return False
