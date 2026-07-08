"""Resolve runtime settings from CLI args, environment, and a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    """Resolved connection settings."""

    url: str
    token: str | None
    input: Path

    @classmethod
    def resolve(
        cls,
        *,
        url: str | None,
        token: str | None,
        input_path: Path | None,
        default_url: str,
        default_input: str,
    ) -> Settings:
        """Resolve settings with precedence: CLI arg > environment/.env > default.

        Args:
            url: ``--url`` value or None.
            token: ``--token`` value or None.
            input_path: ``--input`` value or None.
            default_url: Fallback URL when neither arg nor env provides one.
            default_input: Fallback Skrooge file when neither arg nor env provides one.

        """
        load_dotenv(find_dotenv(usecwd=True))
        resolved_url = url or os.environ.get("FIREFLY_URL") or default_url
        resolved_token = token or os.environ.get("FIREFLY_TOKEN")
        resolved_input = input_path or Path(os.environ.get("SKROOGE_FILE") or default_input)
        return cls(url=resolved_url, token=resolved_token, input=resolved_input)

    def require_token(self) -> str:
        """Return the token or raise :class:`ConfigError` if it is missing."""
        if not self.token:
            raise ConfigError("No Firefly token. Set FIREFLY_TOKEN (env or .env) or pass --token.")
        return self.token
