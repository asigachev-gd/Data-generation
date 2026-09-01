"""Initialize required database metadata before the web process starts."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.observability import configure_logging, configure_telemetry
from app.persistence import DatasetStore


def main() -> int:
    """Apply idempotent application metadata setup after PostgreSQL is healthy."""

    configure_logging()
    settings = get_settings()
    configure_telemetry(settings)
    DatasetStore(settings.database_dsn).initialize()
    logging.getLogger("app").info("database metadata initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
