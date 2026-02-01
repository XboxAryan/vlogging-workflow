# Video Creator - Implementation Details

## Overview

The Video Creator is an interactive terminal menu system that allows users to provide context for AI-assisted video editing. It collects metadata (title, description, instructions, tone) and preferences (music, subtitles) before running the video pipeline, resulting in more relevant AI editing decisions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    video_creator.py                              │
│                   (Main Menu Entry)                              │
│                                                                  │
│  - Interactive questionary-based menu                            │
│  - Collects user context (title, description, instructions)      │
│  - Orchestrates the full pipeline                                │
└─────────────────────┬───────────────────────────────────────────┘
                      │
         ┌────────────┼────────────┬────────────────┐
         ▼            ▼            ▼                ▼
┌─────────────┐ ┌──────────────┐ ┌─────────────┐ ┌─────────────┐
│ session.py  │ │ music_       │ │ subtitle_   │ │ config.yaml │
│             │ │ manager.py   │ │ generator.py│ │             │
│ - Session   │ │              │ │             │ │ - Music     │
│   state     │ │ - Browse     │ │ - Transcript│ │   settings  │
│ - Persist   │ │   music/     │ │   to SRT    │ │ - Subtitle  │
│   to disk   │ │ - Metadata   │ │ - Timestamp │ │   styling   │
│ - Resume    │ │   extraction │ │   adjustment│ │             │
└─────────────┘ └──────────────┘ └─────────────┘ └─────────────┘
         │            │                │
         └────────────┼────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│              Existing Pipeline (Modified)                        │
│                                                                  │
│  ingest.py → transcribe.py → analyze.py* → edit.py* → upload.py │
│                                                                  │
│  * = Modified to receive user context                            │
│                                                                  │
│  analyze.py changes:                                             │
│    - build_prompt() accepts user_context parameter               │
│    - Injects title, description, instructions into AI prompt     │
│    - Tone-specific guidance added to prompt                      │
│                                                                  │
│  edit.py changes:                                                │
│    - add_background_music() - FFmpeg audio mixing                │
│    - burn_subtitles() - FFmpeg subtitle burn-in                  │
│    - CLI flags: --music, --music-volume, --subtitles             │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```
vlogging-workflow/
├── scripts/
│   ├── video_creator.py      # NEW - Main menu interface
│   ├── session.py            # NEW - Session state management
│   ├── music_manager.py      # NEW - Music library browser
│   ├── subtitle_generator.py # NEW - Transcript to SRT converter
│   ├── analyze.py            # MODIFIED - Added user_context support
│   ├── edit.py               # MODIFIED - Added music/subtitle functions
│   ├── pipeline.py           # UNCHANGED - Legacy quick pipeline
│   ├── ingest.py             # UNCHANGED
│   ├── transcribe.py         # UNCHANGED
│   └── upload.py             # UNCHANGED
├── docs/
│   ├── planning.md           # High-level plan
│   ├── IMPLEMENTATION.md     # This file
│   └── sessions/             # Per-video session logs
├── music/                    # Background music library
├── config.yaml               # MODIFIED - Added music/subtitle sections
└── requirements.txt          # MODIFIED - Added new dependencies
```

---

## New Files

### 1. video_creator.py

**Purpose:** Main entry point providing an interactive terminal menu.

**Key Components:**

```python
# Menu structure
main_menu()
├── create_new_video()      # Full context collection flow
│   ├── collect_user_context()
│   │   ├── questionary.text() - title
│   │   ├── questionary.text() - description
│   │   ├── questionary.text() - AI instructions
│   │   ├── questionary.select() - tone
│   │   ├── select_music()
│   │   └── questionary.confirm() - subtitles
│   ├── show_context_summary()
│   └── run_full_pipeline()
├── continue_session()      # Resume incomplete sessions
├── quick_pipeline()        # Legacy mode (no context)
├── show_sessions_history() # View past sessions
└── show_settings()         # Display current config
```

**Pipeline Execution Flow:**

```python
def run_full_pipeline(session, session_manager, config):
    # 1. Ingest footage from SD card
    run_pipeline_step("ingest.py")

    # 2. Transcribe audio
    run_pipeline_step("transcribe.py", [project_path])

    # 3. Save user context to JSON for analyze.py
    context_path = project_path / "user_context.json"
    with open(context_path, 'w') as f:
        json.dump(context.to_dict(), f)

    # 4. Analyze with context
    run_pipeline_step("analyze.py", [
        project_path,
        "--context", context_path
    ])

    # 5. Edit with music/subtitles
    edit_args = [project_path]
    if context.music.enabled:
        edit_args += ["--music", context.music.track]
        edit_args += ["--music-volume", str(context.music.volume)]
    if context.subtitles.enabled:
        edit_args += ["--subtitles"]

    run_pipeline_step("edit.py", edit_args)
```

---

### 2. session.py

**Purpose:** Manage session state with persistence and recovery.

**Data Classes:**

```python
@dataclass
class MusicConfig:
    enabled: bool = False
    track: Optional[str] = None
    volume: float = 0.15

@dataclass
class SubtitleConfig:
    enabled: bool = False

@dataclass
class UserContext:
    title: str = ""
    description: str = ""
    instructions: str = ""
    tone: str = "general"  # general, calm, energetic, informative, funny, cinematic
    music: MusicConfig
    subtitles: SubtitleConfig

@dataclass
class Session:
    id: str                    # e.g., "2026-01-17_morning-coffee"
    created_at: str            # ISO timestamp
    updated_at: str            # ISO timestamp
    status: str                # "in_progress", "completed", "failed"
    current_step: str          # "setup", "ingest", "transcribe", "analyze", "edit", "done"
    user_context: UserContext
    project_path: Optional[str]
    output_video: Optional[str]
    error: Optional[str]
```

**SessionManager Methods:**

```python
class SessionManager:
    def create_session(user_context: UserContext) -> Session
    def update_session(session, step=None, status=None, ...) -> Session
    def load_session(session_id: str) -> Optional[Session]
    def list_sessions(status: Optional[str] = None) -> list[Session]
    def get_incomplete_sessions() -> list[Session]
    def save_ai_prompt(session: Session, prompt: str) -> None
```

**Session Directory Structure:**

```
docs/sessions/2026-01-17_morning-coffee/
├── session.json      # Serialized Session object
├── progress.log      # Timestamped progress entries
└── ai_prompt.md      # The actual prompt sent to AI (for debugging)
```

**Example session.json:**

```json
{
  "id": "2026-01-17_morning-coffee",
  "created_at": "2026-01-17T09:30:00",
  "updated_at": "2026-01-17T09:45:00",
  "status": "completed",
  "current_step": "done",
  "user_context": {
    "title": "Morning Coffee & Code",
    "description": "A calm morning vlog about my coffee ritual",
    "instructions": "Keep it slow-paced, prioritize quiet moments",
    "tone": "calm",
    "music": {
      "enabled": true,
      "track": "/path/to/music/lo-fi-beats.mp3",
      "volume": 0.15
    },
    "subtitles": {
      "enabled": true
    }
  },
  "project_path": "/path/to/projects/2026-01-17",
  "output_video": "/path/to/projects/2026-01-17/output/vlog_2026-01-17.mp4",
  "error": null
}
```

---

### 3. music_manager.py

**Purpose:** Browse and select background music from the library.

**Supported Formats:** `.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.aac`

**Key Components:**

```python
@dataclass
class MusicTrack:
    path: str
    filename: str
    duration: Optional[float]  # From mutagen
    title: Optional[str]       # ID3 tag
    artist: Optional[str]      # ID3 tag

class MusicManager:
    def __init__(self, library_path: Path)
    def list_tracks(self) -> list[MusicTrack]
    def get_track(self, filename: str) -> Optional[MusicTrack]
    def format_track_choice(self, track: MusicTrack) -> str
    def is_empty(self) -> bool
```

**Metadata Extraction:**

Uses `mutagen` library to extract:
- Duration (all formats)
- Title and Artist (MP3 ID3 tags)

```python
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3

audio = MutagenFile(file_path)
track.duration = audio.info.length

tags = EasyID3(file_path)
track.title = tags.get("title", [None])[0]
track.artist = tags.get("artist", [None])[0]
```

---

### 4. subtitle_generator.py

**Purpose:** Convert transcripts to SRT subtitle format.

**Key Components:**

```python
@dataclass
class SubtitleEntry:
    index: int
    start_time: float
    end_time: float
    text: str

class SubtitleGenerator:
    max_chars_per_line: int = 42
    max_lines: int = 2

    def generate_srt(transcript_path, output_path) -> bool
    def generate_srt_from_analysis(transcript_path, analysis_path, output_path) -> bool
```

**Timestamp Adjustment Logic:**

When using analysis (selected segments only), timestamps must be adjusted to match the final video timeline:

```python
def _adjust_timestamps(segments, selected_segments):
    # Build cumulative offset for each selected segment
    cumulative_offset = 0
    offset_map = {}

    for sel in selected_segments:
        key = (sel['file'], sel['start'], sel['end'])
        offset_map[key] = cumulative_offset
        cumulative_offset += sel['end'] - sel['start']

    # Adjust each transcript segment
    for seg in segments:
        selection = seg['_selection']
        base_offset = offset_map[selection_key]

        # New timestamp = offset + (original - selection_start)
        new_start = base_offset + (seg['start'] - selection['start'])
        new_end = base_offset + (seg['end'] - selection['start'])
```

**SRT Format:**

```
1
00:00:02,500 --> 00:00:05,800
Hello and welcome to
my morning vlog

2
00:00:06,100 --> 00:00:09,400
Today I'm making coffee
and writing some code
```

---

## Modified Files

### 1. analyze.py

**Changes:** Added `user_context` parameter to inject creator context into AI prompt.

**Modified Functions:**

```python
def build_prompt(transcript_data, config, max_segments=100, user_context=None):
    # ... existing code ...

    # NEW: Build user context section
    context_section = ""
    if user_context:
        context_parts = []
        if user_context.get('title'):
            context_parts.append(f"- Title: {user_context['title']}")
        if user_context.get('description'):
            context_parts.append(f"- Description: {user_context['description']}")
        if user_context.get('instructions'):
            context_parts.append(f"- Creator Instructions: {user_context['instructions']}")
        if user_context.get('tone'):
            # Tone-specific guidance
            tone_guidance = {
                'calm': 'Keep a relaxed, peaceful pace. Prioritize quiet moments.',
                'energetic': 'Keep it fast-paced and exciting. Prioritize action.',
                'informative': 'Focus on clear explanations and demonstrations.',
                'funny': 'Prioritize comedic moments, reactions, and humor.',
                'cinematic': 'Focus on visually interesting shots and story.',
            }
            # ... add to context_parts

        context_section = f"""
VIDEO CONTEXT FROM CREATOR:
{chr(10).join(context_parts)}

Use this context to guide your clip selection.
"""

    return f"""Create clips for a {target_duration}-second daily vlog...
{context_section}
TRANSCRIPT BY FILE:
{segments_text}
...
"""

def analyze_with_openrouter(transcript_data, config, user_context=None):
    prompt = build_prompt(transcript_data, config, user_context=user_context)
    # ... rest unchanged

def analyze_with_anthropic(transcript_data, config, user_context=None):
    prompt = build_prompt(transcript_data, config, user_context=user_context)
    # ... rest unchanged

def analyze_with_ai(transcript_data, config, user_context=None):
    # Routes to appropriate provider with user_context

def process_project(project_path, config, use_fallback=False, user_context=None):
    # Passes user_context through to analyze_with_ai

def main():
    # NEW: Parse --context flag
    if "--context" in sys.argv:
        context_idx = sys.argv.index("--context")
        context_path = Path(sys.argv[context_idx + 1])
        with open(context_path, 'r') as f:
            user_context = json.load(f)
```

**Example Enhanced Prompt:**

```
Create clips for a 540-second daily vlog from 3600s of footage.

VIDEO CONTEXT FROM CREATOR:
- Title: Morning Coffee & Code
- Description: A calm morning vlog about my coffee ritual
- Creator Instructions: Keep it slow-paced, prioritize quiet moments
- Tone: calm - Keep a relaxed, peaceful pace. Prioritize quiet moments.

Use this context to guide your clip selection.

TRANSCRIPT BY FILE:
=== DJI_0001.MP4 (total: 1200s) ===
  0.0-5.2: Good morning everyone
  12.4-18.1: Let me make some coffee first
  ...
```

---

### 2. edit.py

**Changes:** Added background music mixing and subtitle burning.

**New Functions:**

#### add_background_music()

```python
def add_background_music(video_path, output_path, music_path, config, volume=0.15):
    """Mix background music into video using FFmpeg."""

    # Get config
    fade_in = config.get('music', {}).get('fade_in', 2)
    fade_out = config.get('music', {}).get('fade_out', 2)

    # Get video duration for fade out timing
    video_duration = get_video_duration(video_path)
    fade_out_start = video_duration - fade_out

    # FFmpeg filter chain:
    # 1. Loop music infinitely, trim to video length
    # 2. Apply volume
    # 3. Fade in at start
    # 4. Fade out at end
    # 5. Mix with original audio
    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{video_duration},"
        f"volume={volume},"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start}:d={fade_out}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-i', music_path,
        '-filter_complex', audio_filter,
        '-map', '0:v',           # Keep video from first input
        '-map', '[aout]',        # Use mixed audio
        '-c:v', 'copy',          # Don't re-encode video
        '-c:a', 'aac', '-b:a', '192k',
        '-y', output_path
    ]
```

**FFmpeg Audio Filter Breakdown:**

```
[1:a]                           # Select audio from second input (music)
aloop=loop=-1:size=2e+09        # Loop infinitely (size=2GB buffer)
atrim=0:{duration}              # Trim to video length
volume={vol}                    # Apply volume (e.g., 0.15)
afade=t=in:st=0:d=2             # Fade in over 2 seconds
afade=t=out:st={end-2}:d=2      # Fade out last 2 seconds
[music];                        # Label this stream "music"

[0:a][music]                    # Take original audio and music
amix=inputs=2                   # Mix them together
:duration=first                 # Output duration = first input (video)
:dropout_transition=2           # Smooth transition if one ends
[aout]                          # Label output "aout"
```

#### burn_subtitles()

```python
def burn_subtitles(video_path, output_path, srt_path, config):
    """Burn SRT subtitles into video using FFmpeg."""

    # Get config
    font_size = config.get('subtitles', {}).get('font_size', 24)
    margin = config.get('subtitles', {}).get('margin', 40)
    outline_width = config.get('subtitles', {}).get('outline_width', 2)

    # Escape path for FFmpeg filter
    srt_escaped = str(srt_path).replace(':', '\\:')

    subtitle_filter = (
        f"subtitles='{srt_escaped}':"
        f"force_style='FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"      # White (ABGR format)
        f"OutlineColour=&H00000000,"      # Black outline
        f"BorderStyle=1,"                  # Outline + shadow
        f"Outline={outline_width},"
        f"MarginV={margin}'"
    )

    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-vf', subtitle_filter,
        '-c:a', 'copy',           # Keep audio unchanged
        '-c:v', 'h264',           # Re-encode video (required for subtitles)
        '-preset', 'medium',
        '-crf', '23',
        '-y', output_path
    ]
```

**Modified process_project():**

```python
def process_project(project_path, config, music_path=None, music_volume=0.15, enable_subtitles=False):
    # ... existing segment extraction and concatenation ...

    # Add intro/outro
    add_intro_outro(concat_output, with_intro_outro, config)
    current_video = with_intro_outro

    # NEW: Add background music if requested
    if music_path and Path(music_path).exists():
        with_music = output_dir / f"with_music_{date}.mp4"
        if add_background_music(current_video, with_music, music_path, config, volume=music_volume):
            current_video = with_music

    # NEW: Burn subtitles if requested
    if enable_subtitles:
        from subtitle_generator import generate_subtitles
        srt_path = generate_subtitles(project_path, use_analysis=True)

        if srt_path and srt_path.exists():
            with_subs = output_dir / f"with_subs_{date}.mp4"
            if burn_subtitles(current_video, with_subs, srt_path, config):
                current_video = with_subs

    # Rename to final output
    shutil.move(current_video, final_output)

    # Cleanup intermediate files
    for pattern in ["concat_*.mp4", "with_intro_*.mp4", "with_music_*.mp4", "with_subs_*.mp4"]:
        # ... delete intermediates
```

**New CLI Arguments:**

```bash
python edit.py <project_path> [--music <path>] [--music-volume <0.0-1.0>] [--subtitles]
```

---

### 3. config.yaml

**New Sections:**

```yaml
# Music Settings
music:
  # Directory containing background music files
  library_path: "music"
  # Default volume for background music (0.0-1.0)
  default_volume: 0.15
  # Fade in duration (seconds)
  fade_in: 2
  # Fade out duration (seconds)
  fade_out: 2

# Subtitle Settings
subtitles:
  # Font size for burned subtitles
  font_size: 24
  # Font color (FFmpeg format)
  font_color: "white"
  # Outline color for readability
  outline_color: "black"
  # Outline thickness
  outline_width: 2
  # Position: bottom, top, center
  position: "bottom"
  # Margin from edge (pixels)
  margin: 40
```

---

### 4. requirements.txt

**New Dependencies:**

```
# Video Creator Menu
questionary>=2.0.0    # Interactive terminal prompts
mutagen>=1.47.0       # Audio file metadata extraction
pysrt>=1.1.2          # SRT subtitle handling (optional, we have our own implementation)
```

---

## Data Flow

### Complete Pipeline Flow

```
User runs: python scripts/video_creator.py

1. MENU: User selects "Create New Video"

2. CONTEXT COLLECTION:
   ├── Title: "Morning Coffee & Code"
   ├── Description: "A calm morning vlog..."
   ├── Instructions: "Keep it slow-paced..."
   ├── Tone: "calm"
   ├── Music: "music/lo-fi-beats.mp3" @ 0.15 volume
   └── Subtitles: enabled

3. SESSION CREATION:
   └── docs/sessions/2026-01-17_morning-coffee/session.json

4. PIPELINE EXECUTION:
   │
   ├── INGEST (ingest.py)
   │   └── projects/2026-01-17/raw/*.MP4
   │
   ├── TRANSCRIBE (transcribe.py)
   │   └── projects/2026-01-17/transcripts/combined.json
   │
   ├── ANALYZE (analyze.py --context user_context.json)
   │   ├── Reads: transcripts/combined.json + user_context.json
   │   ├── Builds enhanced prompt with user context
   │   ├── Calls AI API
   │   └── Writes: projects/2026-01-17/analysis.json
   │
   └── EDIT (edit.py --music ... --subtitles)
       ├── Extracts segments per analysis.json
       ├── Concatenates segments
       ├── Adds intro/outro
       ├── Mixes background music (FFmpeg)
       ├── Generates SRT from transcript
       ├── Burns subtitles (FFmpeg)
       └── Writes: projects/2026-01-17/output/vlog_2026-01-17.mp4

5. SESSION UPDATE:
   └── status: "completed", output_video: "...vlog_2026-01-17.mp4"
```

### User Context Flow

```
UserContext (Python)
       │
       ▼
user_context.json (on disk)
       │
       ▼
analyze.py --context user_context.json
       │
       ▼
build_prompt(user_context=...)
       │
       ▼
Enhanced AI Prompt
       │
       ▼
AI selects segments matching user's vision
```

---

## Error Handling & Recovery

### Session Recovery

If the pipeline fails at any step, the session is preserved:

```python
session_manager.update_session(session, error="Transcription failed")
# Session saved with status="failed", current_step="transcribe"
```

User can later:
1. Select "Continue Previous Session"
2. Choose the failed session
3. Pipeline resumes from the failed step

### Intermediate File Cleanup

On success, all intermediate files are cleaned up:
- `concat_*.mp4`
- `with_intro_*.mp4`
- `with_music_*.mp4`
- `with_subs_*.mp4`
- `temp_segments/*.mp4`

Only the final `vlog_YYYY-MM-DD.mp4` remains.

### FFmpeg Error Handling

```python
result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode != 0:
    console.print(f"[red]FFmpeg error: {result.stderr[:500]}[/red]")
    return False
```

---

## Usage Examples

### Basic Usage

```bash
# Activate environment
source .venv/bin/activate

# Run Video Creator
python scripts/video_creator.py
```

### Direct Pipeline with Context

```bash
# Create context file manually
cat > /tmp/context.json << 'EOF'
{
  "title": "Coding Session",
  "description": "Working on my side project",
  "instructions": "Focus on typing and screen shares",
  "tone": "informative"
}
EOF

# Run analyze with context
python scripts/analyze.py projects/2026-01-17 --context /tmp/context.json

# Run edit with music and subtitles
python scripts/edit.py projects/2026-01-17 \
  --music music/ambient.mp3 \
  --music-volume 0.10 \
  --subtitles
```

### Quick Pipeline (No Context)

```bash
# From Video Creator menu: select "Quick Pipeline"
# Or directly:
python scripts/pipeline.py --skip-upload
```

---

## Troubleshooting

### "No music files found"

Add music files to the `music/` directory:
```bash
cp ~/Music/lo-fi-beats.mp3 music/
```

Supported formats: `.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.aac`

### FFmpeg subtitle error

Ensure the SRT path doesn't contain special characters. The code escapes `:` but other characters may cause issues:
```python
srt_escaped = str(srt_path).replace(':', '\\:').replace("'", "\\'")
```

### Session not found

Check that `docs/sessions/` exists and contains session directories:
```bash
ls -la docs/sessions/
```

### Music volume too loud/quiet

Adjust in the menu when prompted, or set default in `config.yaml`:
```yaml
music:
  default_volume: 0.15  # 0.0 to 1.0
```

---

## Future Improvements

1. **Multiple music tracks** - Different music for different sections
2. **Subtitle styling options** - Font selection, colors via menu
3. **Preview mode** - Generate low-res preview before final render
4. **Batch processing** - Process multiple days at once
5. **YouTube metadata** - Use video title/description for upload
6. **Thumbnail generation** - Auto-generate thumbnails from key frames
