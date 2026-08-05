"""In-memory conversation store with per-session history.

Each session keeps a list of (role, content) message tuples.
Oldest entries are trimmed when max_history is exceeded.
"""

import time
import threading
from collections import OrderedDict


class ConversationStore:
    """Thread-safe in-memory conversation storage with TTL eviction."""

    def __init__(self, max_history: int = 10, timeout: int = 30):
        self._max = max_history
        self._timeout = timeout
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}  # session_id → {"messages": list, "last_active": float}

    def create_session(self, session_id: str) -> None:
        """Create a new empty session."""
        with self._lock:
            self._cleanup()
            self._sessions[session_id] = {
                "messages": [],
                "last_active": time.time(),
            }

    def add_user_message(self, session_id: str, text: str) -> None:
        """Record a user message in the session."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess["messages"].append({"role": "user", "content": text})
            sess["last_active"] = time.time()
            self._trim(sess)

    def add_assistant_message(self, session_id: str, text: str) -> None:
        """Record an assistant response in the session."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess["messages"].append({"role": "assistant", "content": text})
            sess["last_active"] = time.time()
            self._trim(sess)

    def get_context(self, session_id: str) -> list[dict]:
        """Return all messages in the session (for LLM context)."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return []
            sess["last_active"] = time.time()
            return list(sess["messages"])

    def end_session(self, session_id: str) -> None:
        """Remove a session and its history."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def is_active(self, session_id: str) -> bool:
        """Check if a session exists and hasn't timed out."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            if time.time() - sess["last_active"] > self._timeout:
                self._sessions.pop(session_id, None)
                return False
            return True

    def _trim(self, sess: dict) -> None:
        """Remove oldest user/assistant pairs if over max_history."""
        msgs = sess["messages"]
        # Keep system prompt + last N turns (user + assistant = 2 per turn)
        while len(msgs) > self._max * 2 + 1:
            # Remove oldest user+assistant pair (first pair after system msg)
            kept_system = msgs[0] if msgs and msgs[0]["role"] == "system" else None
            msgs.pop(1 if kept_system else 0)
            if len(msgs) > 1:
                msgs.pop(1 if kept_system else 0)

    def _cleanup(self) -> None:
        """Remove all expired sessions."""
        now = time.time()
        expired = [
            sid for sid, sess in self._sessions.items()
            if now - sess["last_active"] > self._timeout
        ]
        for sid in expired:
            del self._sessions[sid]

    @property
    def active_sessions(self) -> list[str]:
        """Return list of active (non-expired) session IDs."""
        with self._lock:
            now = time.time()
            return [
                sid for sid, sess in self._sessions.items()
                if now - sess["last_active"] <= self._timeout
            ]
