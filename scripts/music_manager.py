#!/usr/bin/env python3
"""
Music Manager
=============
Browse and select background music from the music library.
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class MusicTrack:
    path: str
    filename: str
    duration: Optional[float] = None
    title: Optional[str] = None
    artist: Optional[str] = None


class MusicManager:
    SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac"}

    def __init__(self, library_path: Path):
        self.library_path = Path(library_path)
        self.library_path.mkdir(parents=True, exist_ok=True)

    def list_tracks(self) -> list[MusicTrack]:
        """List all music tracks in the library."""
        tracks = []

        if not self.library_path.exists():
            return tracks

        for file_path in sorted(self.library_path.iterdir()):
            if file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                track = self._get_track_info(file_path)
                tracks.append(track)

        return tracks

    def get_track(self, filename: str) -> Optional[MusicTrack]:
        """Get a specific track by filename."""
        track_path = self.library_path / filename
        if track_path.exists():
            return self._get_track_info(track_path)
        return None

    def _get_track_info(self, file_path: Path) -> MusicTrack:
        """Extract track information from file."""
        track = MusicTrack(
            path=str(file_path),
            filename=file_path.name,
        )

        # Try to get metadata using mutagen
        try:
            from mutagen import File as MutagenFile
            from mutagen.mp3 import MP3
            from mutagen.easyid3 import EasyID3

            audio = MutagenFile(file_path)
            if audio is not None:
                track.duration = audio.info.length if hasattr(audio.info, "length") else None

                # Try to get ID3 tags for MP3
                if file_path.suffix.lower() == ".mp3":
                    try:
                        tags = EasyID3(file_path)
                        track.title = tags.get("title", [None])[0]
                        track.artist = tags.get("artist", [None])[0]
                    except Exception:
                        pass
        except ImportError:
            # mutagen not installed, use basic info
            pass
        except Exception:
            # Error reading metadata, use basic info
            pass

        return track

    def format_track_choice(self, track: MusicTrack) -> str:
        """Format track for display in menu."""
        parts = []

        # Title or filename
        if track.title:
            parts.append(track.title)
            if track.artist:
                parts.append(f"by {track.artist}")
        else:
            parts.append(track.filename)

        # Duration
        if track.duration:
            minutes = int(track.duration // 60)
            seconds = int(track.duration % 60)
            parts.append(f"({minutes}:{seconds:02d})")

        return " ".join(parts)

    def is_empty(self) -> bool:
        """Check if music library is empty."""
        return len(self.list_tracks()) == 0
