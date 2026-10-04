"""Environment-backed project configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration values loaded from the process environment or a local .env file."""

    edgar_identity: str | None
    deepseek_api_key: str | None
    deepseek_base_url: str
    tiingo_api_key: str | None


def load_settings() -> Settings:
    """Load the minimal settings required by future integrations."""
    load_dotenv()
    return Settings(
        edgar_identity=os.getenv("EDGAR_IDENTITY"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        tiingo_api_key=os.getenv("TIINGO_API_KEY"),
    )
