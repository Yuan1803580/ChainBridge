from __future__ import annotations

from typing import Any


class MarketplaceSDKError(Exception):
    """Base SDK exception."""


class MarketplaceAPIError(MarketplaceSDKError):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details
