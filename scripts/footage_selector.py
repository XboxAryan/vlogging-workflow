#!/usr/bin/env python3
"""
Footage Selector
================
Browse and select video files from SD card, organized by recording date.
Includes thumbnail generation and video preview capabilities.
"""

import os
import subprocess
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor


# Cache directory for thumbnails
THUMBNAIL_CACHE_DIR = Path.home() / ".cache" / "vlogging-workflow" / "thumbnails"


@dataclass
class VideoFile:
    path: Path
    filename: str
    size_bytes: int
    duration: Optional[float] = None
    creation_date: Optional[datetime] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 * 1024 * 1024)

    @property
    def duration_str(self) -> str:
        if self.duration is None:
            return "??:??"
        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def resolution_str(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"

    @property
    def date_str(self) -> str:
        if self.creation_date:
            return self.creation_date.strftime("%Y-%m-%d")
        return "unknown"

    @property
    def time_str(self) -> str:
        if self.creation_date:
            return self.creation_date.strftime("%H:%M:%S")
        return "unknown"


def get_cache_key(video_path: Path) -> str:
    """Generate a unique cache key for a video file based on path and mtime."""
    stat = video_path.stat()
    key_data = f"{video_path}:{stat.st_size}:{stat.st_mtime}"
    return hashlib.md5(key_data.encode()).hexdigest()[:12]


def get_thumbnail_paths(video_path: Path) -> list[Path]:
    """
    Get paths to cached thumbnails for a video file.

    Returns:
        List of 3 thumbnail paths (may not exist yet).
    """
    cache_key = get_cache_key(video_path)
    base_name = video_path.stem
    return [
        THUMBNAIL_CACHE_DIR / f"{base_name}_{cache_key}_{i}.jpg"
        for i in range(1, 4)
    ]


def generate_thumbnails(video_path: Path, force: bool = False) -> list[Path]:
    """
    Generate 3 thumbnail images from a video at 10%, 50%, and 90% of duration.

    Args:
        video_path: Path to the video file
        force: If True, regenerate even if cached

    Returns:
        List of paths to the generated thumbnails
    """
    thumb_paths = get_thumbnail_paths(video_path)

    # Check if already cached
    if not force and all(p.exists() for p in thumb_paths):
        return thumb_paths

    # Ensure cache directory exists
    THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Get video duration using ffprobe
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if duration <= 0:
            return []
    except Exception:
        return []

    # Extract frames at 10%, 50%, 90% of duration - in parallel
    positions = [0.10, 0.50, 0.90]

    def extract_frame(args):
        """Extract a single frame from video."""
        i, pos = args
        timestamp = duration * pos
        thumb_path = thumb_paths[i]
        try:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", "scale=160:-1",  # 160px wide, maintain aspect
                "-q:v", "5",  # JPEG quality
                str(thumb_path)
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)
            return thumb_path if thumb_path.exists() else None
        except Exception:
            return None

    # Generate all 3 thumbnails in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(extract_frame, enumerate(positions)))

    return [p for p in results if p is not None]


def preview_video(video_path: Path) -> bool:
    """
    Open a video file in QuickTime Player for full preview.

    Args:
        video_path: Path to the video file

    Returns:
        True if QuickTime was launched successfully
    """
    try:
        subprocess.run(
            ["open", "-a", "QuickTime Player", str(video_path)],
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        # QuickTime not available, try generic open
        try:
            subprocess.run(["open", str(video_path)], check=True)
            return True
        except Exception:
            return False


@dataclass
class DayGroup:
    date: str  # YYYY-MM-DD
    files: list[VideoFile] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(f.duration or 0 for f in self.files)

    @property
    def total_duration_str(self) -> str:
        total = self.total_duration
        hours = int(total // 3600)
        minutes = int((total % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def total_size_gb(self) -> float:
        return sum(f.size_bytes for f in self.files) / (1024 * 1024 * 1024)

    @property
    def file_count(self) -> int:
        return len(self.files)


class FootageSelector:
    SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".MP4", ".MOV", ".m4v", ".M4V"}

    def __init__(self, config: dict):
        self.config = config
        self.sd_config = config.get("sd_card", {})
        self.volume_name = self.sd_config.get("volume_name", "Untitled")
        self.footage_path = self.sd_config.get("footage_path", "DCIM/DJI_001")

    def get_sd_card_path(self) -> Optional[Path]:
        """Find the mounted SD card path."""
        # macOS volume path
        volume_path = Path(f"/Volumes/{self.volume_name}")
        if volume_path.exists():
            footage_dir = volume_path / self.footage_path
            if footage_dir.exists():
                return footage_dir
            # Try just the volume root
            return volume_path

        # Try common mount points
        for mount_base in ["/Volumes", "/media", "/mnt"]:
            mount_path = Path(mount_base)
            if mount_path.exists():
                for vol in mount_path.iterdir():
                    if vol.is_dir():
                        footage_dir = vol / self.footage_path
                        if footage_dir.exists():
                            return footage_dir
        return None

    def scan_directory(self, directory: Path) -> list[VideoFile]:
        """Scan a directory for video files."""
        videos = []

        if not directory.exists():
            return videos

        # Find all video files recursively
        for ext in self.SUPPORTED_EXTENSIONS:
            for file_path in directory.rglob(f"*{ext}"):
                if file_path.is_file():
                    video = self._get_video_info(file_path)
                    if video:
                        videos.append(video)

        # Sort by creation date, then filename
        videos.sort(key=lambda v: (v.creation_date or datetime.min, v.filename))
        return videos

    def _get_video_info(self, file_path: Path) -> Optional[VideoFile]:
        """Extract video metadata using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                # Fallback to basic file info
                stat = file_path.stat()
                return VideoFile(
                    path=file_path,
                    filename=file_path.name,
                    size_bytes=stat.st_size,
                    creation_date=datetime.fromtimestamp(stat.st_mtime),
                )

            data = json.loads(result.stdout)
            format_info = data.get("format", {})
            streams = data.get("streams", [])

            # Find video stream
            video_stream = None
            for stream in streams:
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            # Extract creation time from format tags
            creation_date = None
            tags = format_info.get("tags", {})
            creation_time = tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate")
            if creation_time:
                try:
                    # Handle various timestamp formats
                    for fmt in [
                        "%Y-%m-%dT%H:%M:%S.%fZ",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%d %H:%M:%S",
                    ]:
                        try:
                            creation_date = datetime.strptime(creation_time[:26], fmt.replace("%z", "")[:len(creation_time)])
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # Fallback to file modification time
            if creation_date is None:
                creation_date = datetime.fromtimestamp(file_path.stat().st_mtime)

            return VideoFile(
                path=file_path,
                filename=file_path.name,
                size_bytes=int(format_info.get("size", file_path.stat().st_size)),
                duration=float(format_info.get("duration", 0)) if format_info.get("duration") else None,
                creation_date=creation_date,
                width=video_stream.get("width") if video_stream else None,
                height=video_stream.get("height") if video_stream else None,
            )

        except Exception as e:
            # Fallback to basic info
            try:
                stat = file_path.stat()
                return VideoFile(
                    path=file_path,
                    filename=file_path.name,
                    size_bytes=stat.st_size,
                    creation_date=datetime.fromtimestamp(stat.st_mtime),
                )
            except Exception:
                return None

    def group_by_date(self, videos: list[VideoFile]) -> list[DayGroup]:
        """Group videos by recording date."""
        groups = defaultdict(list)

        for video in videos:
            date_key = video.date_str
            groups[date_key].append(video)

        # Convert to DayGroup objects, sorted by date descending (newest first)
        day_groups = []
        for date_str in sorted(groups.keys(), reverse=True):
            group = DayGroup(date=date_str, files=groups[date_str])
            # Sort files within group by time
            group.files.sort(key=lambda v: (v.creation_date or datetime.min))
            day_groups.append(group)

        return day_groups

    def format_day_choice(self, group: DayGroup) -> str:
        """Format a day group for display in menu."""
        return (
            f"{group.date} - {group.file_count} files, "
            f"{group.total_duration_str}, {group.total_size_gb:.1f} GB"
        )

    def format_file_choice(self, video: VideoFile) -> str:
        """Format a video file for display in menu."""
        return (
            f"{video.time_str} | {video.filename} | "
            f"{video.duration_str} | {video.size_mb:.0f} MB | {video.resolution_str}"
        )


def scan_sd_card(config: dict) -> tuple[Optional[Path], list[DayGroup]]:
    """
    Scan SD card and return organized footage.

    Returns:
        Tuple of (sd_card_path, list of DayGroups)
    """
    selector = FootageSelector(config)
    sd_path = selector.get_sd_card_path()

    if sd_path is None:
        return None, []

    videos = selector.scan_directory(sd_path)
    groups = selector.group_by_date(videos)

    return sd_path, groups


if __name__ == "__main__":
    import yaml

    # Load config
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    selector = FootageSelector(config)
    sd_path = selector.get_sd_card_path()

    if sd_path is None:
        print("SD card not found")
    else:
        print(f"Found SD card at: {sd_path}")
        videos = selector.scan_directory(sd_path)
        groups = selector.group_by_date(videos)

        print(f"\nFound {len(videos)} videos across {len(groups)} days:\n")
        for group in groups:
            print(f"  {selector.format_day_choice(group)}")
            for video in group.files:
                print(f"    - {selector.format_file_choice(video)}")
