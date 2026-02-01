#!/usr/bin/env python3
"""
Session Management
==================
Handles video creation session state persistence and recovery.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, asdict, field


@dataclass
class MusicConfig:
    enabled: bool = False
    track: Optional[str] = None
    volume: float = 0.15


@dataclass
class SubtitleConfig:
    enabled: bool = False


@dataclass
class YouTubeUploadItem:
    """Represents a single video in the upload queue."""
    video_path: str
    thumbnail_path: Optional[str] = None
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy_status: str = "private"
    category_id: str = "22"

    def to_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "thumbnail_path": self.thumbnail_path,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "privacy_status": self.privacy_status,
            "category_id": self.category_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "YouTubeUploadItem":
        return cls(
            video_path=data["video_path"],
            thumbnail_path=data.get("thumbnail_path"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            privacy_status=data.get("privacy_status", "private"),
            category_id=data.get("category_id", "22"),
        )


@dataclass
class YouTubeUploadResult:
    """Result of a single upload attempt."""
    video_path: str
    success: bool
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class UserContext:
    title: str = ""
    description: str = ""
    instructions: str = ""
    tone: str = "general"
    music: MusicConfig = field(default_factory=MusicConfig)
    subtitles: SubtitleConfig = field(default_factory=SubtitleConfig)
    source_files: list[str] = field(default_factory=list)  # List of file paths to import
    source_date: str = ""  # Date of the footage (YYYY-MM-DD)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "instructions": self.instructions,
            "tone": self.tone,
            "music": {
                "enabled": self.music.enabled,
                "track": self.music.track,
                "volume": self.music.volume,
            },
            "subtitles": {
                "enabled": self.subtitles.enabled,
            },
            "source_files": self.source_files,
            "source_date": self.source_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserContext":
        music_data = data.get("music", {})
        subtitle_data = data.get("subtitles", {})
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            instructions=data.get("instructions", ""),
            tone=data.get("tone", "general"),
            music=MusicConfig(
                enabled=music_data.get("enabled", False),
                track=music_data.get("track"),
                volume=music_data.get("volume", 0.15),
            ),
            subtitles=SubtitleConfig(
                enabled=subtitle_data.get("enabled", False),
            ),
            source_files=data.get("source_files", []),
            source_date=data.get("source_date", ""),
        )


@dataclass
class Session:
    id: str
    created_at: str
    updated_at: str
    status: str  # "in_progress", "completed", "failed"
    current_step: str  # "setup", "ingest", "transcribe", "analyze", "edit", "upload", "done"
    user_context: UserContext
    project_path: Optional[str] = None
    output_video: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "current_step": self.current_step,
            "user_context": self.user_context.to_dict(),
            "project_path": self.project_path,
            "output_video": self.output_video,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=data["status"],
            current_step=data["current_step"],
            user_context=UserContext.from_dict(data.get("user_context", {})),
            project_path=data.get("project_path"),
            output_video=data.get("output_video"),
            error=data.get("error"),
        )


class SessionManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.sessions_dir = self.base_dir / "docs" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def create_session(self, user_context: UserContext) -> Session:
        """Create a new session with the given user context."""
        now = datetime.now()
        # Create session ID from date and slugified title
        slug = self._slugify(user_context.title) if user_context.title else "untitled"
        session_id = f"{now.strftime('%Y-%m-%d')}_{slug}"

        session = Session(
            id=session_id,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            status="in_progress",
            current_step="setup",
            user_context=user_context,
        )

        # Create session directory
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Save session
        self._save_session(session)
        self._log_progress(session, "Session created")

        return session

    def update_session(
        self,
        session: Session,
        step: Optional[str] = None,
        status: Optional[str] = None,
        project_path: Optional[str] = None,
        output_video: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Session:
        """Update session state and persist."""
        session.updated_at = datetime.now().isoformat()

        if step:
            session.current_step = step
            self._log_progress(session, f"Step: {step}")
        if status:
            session.status = status
        if project_path:
            session.project_path = project_path
        if output_video:
            session.output_video = output_video
        if error:
            session.error = error
            session.status = "failed"
            self._log_progress(session, f"Error: {error}")

        self._save_session(session)
        return session

    def load_session(self, session_id: str) -> Optional[Session]:
        """Load a session by ID."""
        session_path = self.sessions_dir / session_id / "session.json"
        if not session_path.exists():
            return None

        with open(session_path, "r") as f:
            data = json.load(f)
        return Session.from_dict(data)

    def list_sessions(self, status: Optional[str] = None) -> list[Session]:
        """List all sessions, optionally filtered by status."""
        sessions = []
        for session_dir in sorted(self.sessions_dir.iterdir(), reverse=True):
            if session_dir.is_dir():
                session_path = session_dir / "session.json"
                if session_path.exists():
                    session = self.load_session(session_dir.name)
                    if session:
                        if status is None or session.status == status:
                            sessions.append(session)
        return sessions

    def get_incomplete_sessions(self) -> list[Session]:
        """Get sessions that can be resumed."""
        return self.list_sessions(status="in_progress")

    def save_ai_prompt(self, session: Session, prompt: str) -> None:
        """Save the AI prompt used for this session."""
        session_dir = self.sessions_dir / session.id
        prompt_path = session_dir / "ai_prompt.md"
        with open(prompt_path, "w") as f:
            f.write(f"# AI Prompt for {session.user_context.title}\n\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n\n")
            f.write("```\n")
            f.write(prompt)
            f.write("\n```\n")

    def _save_session(self, session: Session) -> None:
        """Persist session to disk."""
        session_dir = self.sessions_dir / session.id
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / "session.json"

        with open(session_path, "w") as f:
            json.dump(session.to_dict(), f, indent=2)

    def _log_progress(self, session: Session, message: str) -> None:
        """Append to session progress log."""
        session_dir = self.sessions_dir / session.id
        log_path = session_dir / "progress.log"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")

    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        import re

        # Convert to lowercase
        slug = text.lower()
        # Replace spaces and underscores with hyphens
        slug = re.sub(r"[\s_]+", "-", slug)
        # Remove non-alphanumeric characters (except hyphens)
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        # Remove multiple consecutive hyphens
        slug = re.sub(r"-+", "-", slug)
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        # Limit length
        return slug[:50] if slug else "untitled"
