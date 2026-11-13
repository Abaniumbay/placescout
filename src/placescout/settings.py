import os
from dataclasses import dataclass

from placescout import __version__

REPO_URL = "https://github.com/Abaniumbay/placescout"


@dataclass(frozen=True)
class Settings:
    geoapify_api_key: str | None = None
    # Wikimedia asks every bot for a way to reach its operator.
    contact: str | None = None
    timeout_seconds: float = 10.0
    max_pages_per_site: int = 3
    max_page_bytes: int = 2_000_000
    max_document_chars: int = 20_000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            geoapify_api_key=os.environ.get("GEOAPIFY_API_KEY") or None,
            contact=os.environ.get("PLACESCOUT_CONTACT") or None,
        )

    @property
    def user_agent(self) -> str:
        details = "; ".join(p for p in (f"+{REPO_URL}", self.contact) if p)
        return f"PlaceScoutBot/{__version__} ({details})"
