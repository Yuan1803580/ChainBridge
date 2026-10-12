from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.inspection import inspect


MONEY_FIELDS = {"amount", "base_price", "budget", "payable_amount", "refund_amount"}


def camel_case(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def decimal_string(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return decimal_string(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {camel_case(key): serialize(item) for key, item in value.items()}
    mapper = inspect(value.__class__, raiseerr=False)
    if mapper is not None:
        result: dict[str, Any] = {}
        for attribute in mapper.column_attrs:
            name = attribute.key
            item = getattr(value, name)
            if name in MONEY_FIELDS and item is not None:
                item = decimal_string(Decimal(item))
            output_name = "metadata" if name == "metadata_json" else camel_case(name)
            result[output_name] = serialize(item)
        return result
    return value
