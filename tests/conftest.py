"""Pytest configuration and fixtures for Kometa tests."""

import logging
from unittest.mock import MagicMock

import modules.util
import modules.cache


def pytest_configure(config):
    """Initialize logger for tests before any test modules are imported."""
    logger = MagicMock(spec=logging.Logger)
    modules.util.logger = logger
    modules.cache.logger = logger
