"""Shared test fixtures."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_redis():
    """Mock Redis client that simulates basic Redis operations."""
    client = MagicMock()
    client.get.return_value = None
    client.delete.return_value = 0
    client.scan.return_value = (0, [])
    return client


@pytest.fixture
def mock_async_redis():
    """Mock async Redis client that simulates basic Redis operations."""
    client = AsyncMock()
    client.get.return_value = None
    client.delete.return_value = 0
    client.scan.return_value = (0, [])
    return client
