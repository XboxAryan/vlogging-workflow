#!/usr/bin/env python3
"""
Subtitle Generator
==================
Convert transcripts to SRT subtitle format.
"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class SubtitleEntry:
    index: int
    start_time: float
    end_time: float
    text: str


class SubtitleGenerator:
    def __init__(self, max_chars_per_line: int = 42, max_lines: int = 2):
        self.max_chars_per_line = max_chars_per_line
        self.max_lines = max_lines

    def generate_srt(self, transcript_path: Path, output_path: Path) -> bool:
        """Generate SRT file from transcript JSON."""
        try:
            with open(transcript_path, "r") as f:
                transcript_data = json.load(f)

            segments = transcript_data.get("all_segments", [])
            if not segments:
                return False

            entries = self._segments_to_entries(segments)
            self._write_srt(entries, output_path)
            return True

        except Exception as e:
            print(f"Error generating subtitles: {e}")
            return False

    def generate_srt_from_analysis(
        self,
        transcript_path: Path,
        analysis_path: Path,
        output_path: Path,
    ) -> bool:
        """Generate SRT from transcript, filtered to only selected segments."""
        try:
            with open(transcript_path, "r") as f:
                transcript_data = json.load(f)

            with open(analysis_path, "r") as f:
                analysis_data = json.load(f)

            all_segments = transcript_data.get("all_segments", [])
            selected_segments = analysis_data.get("selected_segments", [])

            if not all_segments or not selected_segments:
                return False

            # Filter transcript segments to only those within selected video segments
            filtered_segments = self._filter_segments(all_segments, selected_segments)

            # Adjust timestamps relative to final video
            adjusted_segments = self._adjust_timestamps(filtered_segments, selected_segments)

            entries = self._segments_to_entries(adjusted_segments)
            self._write_srt(entries, output_path)
            return True

        except Exception as e:
            print(f"Error generating subtitles: {e}")
            return False

    def _filter_segments(self, transcript_segments: list, selected_segments: list) -> list:
        """Filter transcript segments to only those within selected video segments."""
        filtered = []

        for sel in selected_segments:
            sel_file = sel.get("file", "")
            sel_start = sel.get("start", 0)
            sel_end = sel.get("end", 0)

            for seg in transcript_segments:
                seg_file = seg.get("file", "")
                seg_start = seg.get("start", 0)
                seg_end = seg.get("end", 0)

                # Check if segment is within selected range
                if seg_file == sel_file:
                    if seg_start >= sel_start and seg_end <= sel_end:
                        filtered.append({
                            **seg,
                            "_selection": sel,
                        })

        return filtered

    def _adjust_timestamps(self, segments: list, selected_segments: list) -> list:
        """Adjust segment timestamps relative to final video timeline."""
        adjusted = []
        cumulative_offset = 0

        # Build offset map for each selected segment
        offset_map = {}
        for sel in selected_segments:
            key = (sel.get("file", ""), sel.get("start", 0), sel.get("end", 0))
            offset_map[key] = cumulative_offset
            cumulative_offset += sel.get("end", 0) - sel.get("start", 0)

        for seg in segments:
            selection = seg.get("_selection", {})
            key = (selection.get("file", ""), selection.get("start", 0), selection.get("end", 0))
            base_offset = offset_map.get(key, 0)
            selection_start = selection.get("start", 0)

            # Calculate new timestamps relative to final video
            new_start = base_offset + (seg["start"] - selection_start)
            new_end = base_offset + (seg["end"] - selection_start)

            adjusted.append({
                "start": max(0, new_start),
                "end": new_end,
                "text": seg.get("text", ""),
            })

        return adjusted

    def _segments_to_entries(self, segments: list) -> list[SubtitleEntry]:
        """Convert transcript segments to subtitle entries."""
        entries = []
        index = 1

        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue

            # Wrap text to fit subtitle constraints
            wrapped_text = self._wrap_text(text)

            entry = SubtitleEntry(
                index=index,
                start_time=seg.get("start", 0),
                end_time=seg.get("end", 0),
                text=wrapped_text,
            )
            entries.append(entry)
            index += 1

        return entries

    def _wrap_text(self, text: str) -> str:
        """Wrap text to fit subtitle display constraints."""
        words = text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            word_length = len(word)
            # +1 for space between words
            new_length = current_length + word_length + (1 if current_line else 0)

            if new_length <= self.max_chars_per_line:
                current_line.append(word)
                current_length = new_length
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = word_length

                # Stop if we've hit max lines
                if len(lines) >= self.max_lines:
                    break

        if current_line and len(lines) < self.max_lines:
            lines.append(" ".join(current_line))

        return "\n".join(lines)

    def _write_srt(self, entries: list[SubtitleEntry], output_path: Path) -> None:
        """Write entries to SRT file."""
        with open(output_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(f"{entry.index}\n")
                f.write(f"{self._format_timestamp(entry.start_time)} --> {self._format_timestamp(entry.end_time)}\n")
                f.write(f"{entry.text}\n")
                f.write("\n")

    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_subtitles(project_path: Path, use_analysis: bool = True) -> Optional[Path]:
    """
    Generate SRT subtitles for a project.

    Args:
        project_path: Path to project directory
        use_analysis: If True, only include subtitles for selected segments

    Returns:
        Path to generated SRT file, or None on failure
    """
    project_path = Path(project_path)
    transcript_path = project_path / "transcripts" / "combined.json"
    analysis_path = project_path / "analysis.json"
    output_path = project_path / "output" / "subtitles.srt"

    if not transcript_path.exists():
        print(f"Error: Transcript not found at {transcript_path}")
        return None

    generator = SubtitleGenerator()

    if use_analysis and analysis_path.exists():
        success = generator.generate_srt_from_analysis(
            transcript_path, analysis_path, output_path
        )
    else:
        success = generator.generate_srt(transcript_path, output_path)

    return output_path if success else None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python subtitle_generator.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1])
    result = generate_subtitles(project_path)
    if result:
        print(f"Subtitles generated: {result}")
    else:
        print("Failed to generate subtitles")
        sys.exit(1)
