"""Default serializer and deserializer for cache values."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def default_serializer(data: Any) -> str:
    """Serialize data to JSON string, converting non-serializable types via str()."""
    return json.dumps(data, default=str)


DEFAULT_DESERIALIZER: Callable[[str | bytes], Any] = json.loads
