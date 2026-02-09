"""
SQLite-based session store for conversation history.
"""
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A single chat message in a session."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create from dictionary."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata"),
        )


@dataclass
class SessionMeta:
    """Metadata about a conversation session."""
    session_id: str
    created_at: datetime
    last_active: datetime
    message_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "message_count": self.message_count,
        }


class SessionStore:
    """SQLite-based storage for conversation sessions."""

    def __init__(self, db_path: str = ":memory:"):
        """
        Initialize session store.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory DB
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                last_active TEXT NOT NULL
            )
        """)

        # Messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                    ON DELETE CASCADE
            )
        """)

        # Index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session_id
            ON messages (session_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp
            ON messages (timestamp)
        """)

        conn.commit()
        conn.close()
        logger.info(f"Initialized session store at {self.db_path}")

    def create_session(self) -> str:
        """
        Create a new session.

        Returns:
            session_id: UUID string
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (session_id, created_at, last_active) VALUES (?, ?, ?)",
            (session_id, now, now)
        )
        conn.commit()
        conn.close()

        logger.info(f"Created session {session_id}")
        return session_id

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save a message to a session.

        Args:
            session_id: Session ID
            role: "user" or "assistant"
            content: Message content
            metadata: Optional metadata dictionary

        Raises:
            ValueError: If session doesn't exist
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        if not cursor.fetchone():
            conn.close()
            raise ValueError(f"Session {session_id} not found")

        # Insert message
        now = datetime.utcnow().isoformat()
        metadata_json = json.dumps(metadata) if metadata else None

        cursor.execute(
            """INSERT INTO messages (session_id, role, content, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, role, content, now, metadata_json)
        )

        # Update session last_active
        cursor.execute(
            "UPDATE sessions SET last_active = ? WHERE session_id = ?",
            (now, session_id)
        )

        conn.commit()
        conn.close()
        logger.debug(f"Saved {role} message to session {session_id}")

    def get_history(self, session_id: str, limit: int = 10) -> List[Message]:
        """
        Get message history for a session.

        Args:
            session_id: Session ID
            limit: Maximum number of messages to return (most recent)

        Returns:
            List of Message objects, ordered by timestamp (oldest first)

        Raises:
            ValueError: If session doesn't exist
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        if not cursor.fetchone():
            conn.close()
            raise ValueError(f"Session {session_id} not found")

        # Fetch messages (newest first, then reverse)
        cursor.execute(
            """SELECT role, content, timestamp, metadata
               FROM messages
               WHERE session_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (session_id, limit)
        )

        rows = cursor.fetchall()
        conn.close()

        # Convert to Message objects and reverse to get chronological order
        messages = []
        for row in reversed(rows):
            role, content, timestamp, metadata_json = row
            metadata = json.loads(metadata_json) if metadata_json else None
            messages.append(Message(
                role=role,
                content=content,
                timestamp=datetime.fromisoformat(timestamp),
                metadata=metadata
            ))

        return messages

    def list_sessions(self) -> List[SessionMeta]:
        """
        List all sessions with metadata.

        Returns:
            List of SessionMeta objects, ordered by last_active (most recent first)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT s.session_id, s.created_at, s.last_active, COUNT(m.id) as msg_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id
            ORDER BY s.last_active DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        sessions = []
        for row in rows:
            session_id, created_at, last_active, msg_count = row
            sessions.append(SessionMeta(
                session_id=session_id,
                created_at=datetime.fromisoformat(created_at),
                last_active=datetime.fromisoformat(last_active),
                message_count=msg_count
            ))

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and all its messages.

        Args:
            session_id: Session ID

        Returns:
            True if session was deleted, False if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        deleted = cursor.rowcount > 0

        conn.commit()
        conn.close()

        if deleted:
            logger.info(f"Deleted session {session_id}")
        else:
            logger.warning(f"Session {session_id} not found for deletion")

        return deleted

    def cleanup_expired(self, timeout_minutes: int = 30) -> int:
        """
        Delete sessions that have been inactive for longer than timeout.

        Args:
            timeout_minutes: Inactivity timeout in minutes

        Returns:
            Number of sessions deleted
        """
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        cutoff_iso = cutoff.isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM sessions WHERE last_active < ?",
            (cutoff_iso,)
        )

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} expired sessions")

        return deleted_count

    def session_exists(self, session_id: str) -> bool:
        """
        Check if a session exists.

        Args:
            session_id: Session ID

        Returns:
            True if session exists, False otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
