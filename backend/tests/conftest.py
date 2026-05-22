"""
Contact-Ops test configuration.
"""

import asyncio
import os

# Use test database URL if set, otherwise default
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://woodpecker:woodpecker@localhost:5432/contact_ops_test",
)
os.environ.setdefault("STANDALONE_MODE", "true")
