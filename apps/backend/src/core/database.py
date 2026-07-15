"""Re-export from src.shared.database for backward compatibility with impl files."""

from src.shared.database import check_db_health, connect_db, db, disconnect_db

__all__ = ["db", "connect_db", "disconnect_db", "check_db_health"]
