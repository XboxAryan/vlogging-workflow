# Vlogging Workflow - How It Works

This document explains the automated vlogging pipeline, its architecture, design decisions, and lessons learned during development.

## Purpose

The goal is to create a **minimal-friction system** for daily life documentation:

1. Shoot footage throughout the day with a DJI Osmo Pocket 3
2. Insert the SD card into your Mac
3. Run a pipeline that automatically creates an 8-10 minute edited video
4. Upload to YouTube as a private video for personal archival

The system prioritizes **automation over perfection** - the aim is consistent daily documentation rather than polished productions.

## Architecture Overview

```
SD Card → Ingest → Transcribe → Analyze → Edit → Upload → Cleanup
            ↓          ↓           ↓         ↓        ↓
         raw/     transcripts/  analysis.json  output/  archive/
```

Each step is a standalone Python script that can be run independently or chained together via `pipeline.py`.

## Directory Structure

```
vlogging-workflow/
├── scripts/           # Pipeline scripts
│   ├── ingest.py      # Copy footage from SD card
│   ├── transcribe.py  # Audio transcription with Whisper
│   ├── analyze.py     # AI-powered highlight selection
│   ├── edit.py        # FFmpeg video editing
│   ├── upload.py      # YouTube upload
│   ├── cleanup.py     # Archive/delete raw files
│   └── pipeline.py    # Run all steps in sequence
├── projects/          # One folder per day
│   └── 2026-01-12/
│       ├── raw/           # Original video files
│       ├── transcripts/   # Whisper output
│       ├── output/        # Final edited video
│       ├── manifest.json  # Project metadata
│       └── analysis.json  # AI analysis results
├── templates/         # Intro/outro videos (optional)
├── config.yaml        # All configuration
├── .env               # API keys (not in git)
└── requirements.txt   # Python dependencies
```

## The Pipeline Steps

### Step 1: Ingest (`scripts/ingest.py`)

**Purpose**: Copy footage from SD card to project directory.

**How it works**:
- Detects SD card at `/Volumes/{volume_name}` (configured in config.yaml)
- Finds video files in `DCIM/DJI_001/` (DJI Osmo Pocket 3 default path)
- Groups files by date extracted from filename (e.g., `DJI_20260112171406_0070_D.MP4`)
- Creates a project directory named by date (e.g., `projects/2026-01-12/`)
- Copies files to `raw/` subdirectory
- Uses `ffprobe` to extract duration metadata
- Creates `manifest.json` with file inventory

**Usage**:
```bash
python scripts/ingest.py                    # Auto-detect SD card
python scripts/ingest.py --date 2026-01-12  # Filter to specific date
python scripts/ingest.py --dry-run          # Preview without copying
```

**First test results**: Successfully copied 16 files (16.7GB) from a day's footage.

### Step 2: Transcribe (`scripts/transcribe.py`)

**Purpose**: Convert audio to text for AI analysis.

**How it works**:
- Uses OpenAI's Whisper model (locally via `faster-whisper`)
- Processes each video file in `raw/`
- Outputs per-file JSON with timestamped segments
- Creates `combined.json` merging all segments with source file references

**Configuration** (in config.yaml):
```yaml
transcription:
  model_size: "base"  # Options: tiny, base, small, medium, large
  language: "en"
```

**Usage**:
```bash
python scripts/transcribe.py projects/2026-01-12
```

**First test results**: Processed 16 files, extracted 379 segments of speech.

### Step 3: Analyze (`scripts/analyze.py`)

**Purpose**: Select the best segments to include in the final video.

**How it works**:
1. Loads transcript segments from `combined.json`
2. Cleans segments (filters explicit content, removes music transcription artifacts)
3. Sends to AI for intelligent selection
4. Falls back to speech-activity-based selection if AI fails
5. Outputs `analysis.json` with selected segments

**The AI Challenge**:

We initially tried using free OpenRouter models for AI analysis:

| Model | Result |
|-------|--------|
| `deepseek/deepseek-r1-0528:free` | Empty responses |
| `qwen/qwen3-4b:free` | Empty responses (finish_reason: length) |
| `meta-llama/llama-3.2-3b-instruct:free` | Returns responses but malformed JSON |

The free Llama model would return two JSON objects instead of one, or truncated responses, causing parse errors. We added JSON repair logic and response normalization, but the model remains unreliable for structured output.

**Current behavior**: When AI fails, the fallback analyzer selects all segments containing substantial speech (5+ words), sorted by amount of speech, until reaching the target duration.

**Content Filtering**:

During testing, we discovered that Whisper transcribes background music lyrics, which sometimes contain explicit content. This caused AI models to refuse processing. We added `clean_transcript_segment()` to filter:
- Segments with explicit words
- Music-like patterns (high word repetition)
- Very short segments (<5 words)

**Usage**:
```bash
python scripts/analyze.py projects/2026-01-12
python scripts/analyze.py --fallback  # Skip AI, use speech-activity selection
```

**First test results**: AI returned malformed JSON, fell back to selecting 151 segments (9 minutes total).

### Step 4: Edit (`scripts/edit.py`)

**Purpose**: Assemble selected segments into a final video.

**How it works**:
1. Reads `analysis.json` for segment selections
2. Extracts each segment using FFmpeg with re-encoding to target resolution
3. Creates temporary segment files in `output/temp_segments/`
4. Concatenates all segments using FFmpeg concat demuxer
5. Optionally prepends intro and appends outro from `templates/`
6. Cleans up temporary files
7. Updates `manifest.json` with output info

**Configuration** (in config.yaml):
```yaml
output:
  resolution: "1080p"  # Options: original, 4k, 1080p, 720p, 480p
  fps: 30
  codec: "h264"
  preset: "medium"     # FFmpeg preset (ultrafast to veryslow)
  crf: 23              # Quality (18-28, lower = better)
```

**Bug fix during testing**:

The initial implementation wrote relative paths to FFmpeg's concat file, causing "No such file or directory" errors. Fixed by converting to absolute paths:

```python
# Before (broken)
f.write(f"file '{path}'\n")

# After (working)
abs_path = Path(path).resolve()
f.write(f"file '{abs_path}'\n")
```

**Usage**:
```bash
python scripts/edit.py projects/2026-01-12
```

**First test results**:
- Processed 151 segments in ~8 minutes
- Output: 9:01 minute video, 352MB, 1080p @ 30fps
- Location: `projects/2026-01-12/output/vlog_2026-01-12.mp4`

### Step 5: Upload (`scripts/upload.py`)

**Purpose**: Upload the final video to YouTube.

**How it works**:
1. Authenticates with YouTube Data API v3 via OAuth
2. Reads video metadata from manifest and analysis
3. Constructs title from template (e.g., "Daily Vlog - 2026-01-12")
4. Uploads with configurable privacy status
5. Updates manifest with video URL

**Setup required**:
1. Create project at https://console.cloud.google.com/
2. Enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop application)
4. Download `credentials.json` to project root
5. First run will open browser for authorization

**Configuration** (in config.yaml):
```yaml
youtube:
  privacy_status: "private"  # Options: private, unlisted, public
  title_template: "Daily Vlog - {date}"
  description: "Auto-generated daily vlog"
  category_id: "22"  # People & Blogs
  tags: ["vlog", "daily", "journal"]
```

**Usage**:
```bash
python scripts/upload.py projects/2026-01-12
python scripts/upload.py --dry-run  # Preview without uploading
```

**First test results**: Dry-run successful, shows correct metadata.

### Step 6: Cleanup (`scripts/cleanup.py`)

**Purpose**: Archive or delete raw footage after successful upload.

**Configuration** (in config.yaml):
```yaml
cleanup:
  action: "archive"  # Options: keep, archive, delete
  archive_format: "zip"
  delete_archives_after_days: 30
```

## Running the Full Pipeline

```bash
# Activate virtual environment
source .venv/bin/activate

# Run complete pipeline
python scripts/pipeline.py

# Or run individual steps
python scripts/ingest.py
python scripts/transcribe.py projects/2026-01-12
python scripts/analyze.py projects/2026-01-12
python scripts/edit.py projects/2026-01-12
python scripts/upload.py projects/2026-01-12 --dry-run
```

## Environment Setup

This project uses `uv` for Python package management (not pip directly).

```bash
# Create virtual environment
uv venv

# Activate it
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

**System dependencies**:
```bash
brew install ffmpeg  # Required for video processing
```

**API Keys** (create `.env` file):
```
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

Get a free OpenRouter key at: https://openrouter.ai/keys

## Known Issues and Future Improvements

### Current Limitations

1. **AI Selection Unreliable**: Free OpenRouter models don't reliably output valid JSON. The fallback works but selects every speech segment rather than intelligently choosing highlights.

2. **Many Short Clips**: The fallback analyzer often produces 100+ clips of 2-6 seconds each, which can feel choppy. A smarter approach would merge adjacent segments or prefer longer clips.

3. **No Visual Analysis**: Selection is purely audio-based. Interesting visual moments without speech are missed.

### Potential Improvements

1. **Better AI Integration**:
   - Use a paid model (Claude, GPT-4) for reliable JSON output
   - Run a local model via Ollama
   - Implement retry logic with different prompt strategies

2. **Smarter Fallback**:
   - Merge adjacent segments from the same file
   - Prefer longer continuous segments over many short ones
   - Weight segments by position (first/last segments of each file often contain context)

3. **Visual Analysis**:
   - Scene detection to find visual variety
   - Face detection to prioritize people-focused moments
   - Motion detection to find action sequences

4. **Automation**:
   - launchd agent to auto-run on SD card insertion
   - Scheduled cleanup of old archives
   - Notification when upload completes

## Development History

This project was built in a single session with the following progression:

1. **Initial design**: Chose a minimal viable pipeline over more complex alternatives (multi-camera, scene detection, etc.)

2. **API key confusion**: Initially attempted to use a Claude Code proxy key with Anthropic API, then clarified to use OpenRouter free models.

3. **Free model testing**: Tried multiple free models (DeepSeek, Qwen, Llama) before settling on Llama 3.2 3B which at least returns responses.

4. **Content moderation issues**: Discovered that transcribed music lyrics triggered AI refusal, added filtering.

5. **JSON parsing**: Llama returns malformed JSON, added repair functions and normalization layer.

6. **Path handling bug**: FFmpeg concat failed due to relative paths, fixed with absolute path conversion.

7. **Successful test**: Full pipeline ran on 16 files (16.7GB) of real footage, producing a 9-minute video.

---

*Last updated: 2026-01-12*
*First successful pipeline test: 2026-01-12*
