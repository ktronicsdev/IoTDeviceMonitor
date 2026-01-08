"""
Centralized configuration for all monitoring scripts.

This module provides a single source of truth for file paths and configuration
to avoid duplication and ensure consistency across all scripts.
"""

from pathlib import Path

# Central credentials file path (relative to project root)
CREDENTIALS_PATH = Path("src/main/java/org/ktronics/config/credentials.json")

def get_credentials_path():
    """
    Get the absolute path to credentials.json.

    Returns:
        Path: Absolute path to credentials.json file
    """
    return CREDENTIALS_PATH.resolve()
