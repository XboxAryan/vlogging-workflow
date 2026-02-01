# Vlogging Workflow

Automated daily vlog pipeline for DJI Osmo Pocket 3. Transforms raw 4K footage into edited 8-10 minute videos uploaded to YouTube as private videos.

## Overview

```
SD Card → Ingest → Transcribe → AI Analysis → Edit → Upload → Cleanup
```

## Requirements

- macOS
- Python 3.9+
- FFmpeg (`brew install ffmpeg`)
- Anthropic API key (for AI analysis)
- Google Cloud credentials (for YouTube upload)

## Quick Start

### 1. Install dependencies

```bash
cd vlogging-workflow
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up API keys

```bash
# Anthropic API for AI analysis
export ANTHROPIC_API_KEY="your-key-here"
```

### 3. Configure your SD card

Edit `config.yaml` and update the `sd_card.volume_name` to match your SD card's name (visible in Finder when mounted).

### 4. Set up YouTube API (optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable YouTube Data API v3
4. Create OAuth 2.0 credentials (Desktop application)
5. Download as `credentials.json` and place in project root

### 5. Run the pipeline

```bash
# Full automatic pipeline
python scripts/pipeline.py

# Or run steps individually
python scripts/ingest.py
python scripts/transcribe.py
python scripts/analyze.py
python scripts/edit.py
python scripts/upload.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `watch_sd.py` | Monitors for SD card insertion and auto-triggers pipeline |
| `ingest.py` | Copies footage from SD card, organizes by date |
| `transcribe.py` | Extracts audio and transcribes using Whisper |
| `analyze.py` | Uses Claude to identify highlight segments |
| `edit.py` | Cuts and concatenates segments using FFmpeg |
| `upload.py` | Uploads to YouTube as private video |
| `pipeline.py` | Runs complete workflow |
| `cleanup.py` | Archives/deletes raw footage after upload |

## Usage Modes

### Manual Mode

Run the pipeline when you're ready:

```bash
python scripts/pipeline.py
```

### Watch Mode

Auto-trigger when SD card is inserted:

```bash
python scripts/watch_sd.py
```

### Step-by-Step

Run individual steps for more control:

```bash
# Just ingest and see what's there
python scripts/ingest.py --dry-run

# Transcribe specific project
python scripts/transcribe.py projects/2024-01-15

# Use fallback analysis (no AI)
python scripts/analyze.py --fallback

# Skip upload
python scripts/pipeline.py --skip-upload
```

## Configuration

Edit `config.yaml` to customize:

- **SD card**: Volume name, footage path
- **Output**: Resolution, quality, frame rate
- **YouTube**: Privacy status, title template, tags
- **AI**: Model, highlight ratio
- **Cleanup**: Archive vs delete, retention period

## Project Structure

After running, each day's project looks like:

```
projects/
└── 2024-01-15/
    ├── raw/              # Original 4K footage
    ├── audio/            # Extracted audio files
    ├── transcripts/      # Whisper transcriptions
    ├── output/           # Final edited video
    ├── manifest.json     # Project metadata
    └── analysis.json     # AI highlight selections
```

## Adding Intro/Outro

Place your intro and outro videos in the templates folder:

```
templates/
├── intro.mp4
└── outro.mp4
```

They'll be automatically added to each video.

## Storage Management

After successful upload, run cleanup to free space:

```bash
# See what would be cleaned
python scripts/cleanup.py --dry-run

# Actually clean up
python scripts/cleanup.py
```

Configure cleanup behavior in `config.yaml`:
- `keep`: Don't delete anything
- `archive`: Compress raw footage to archive folder
- `delete`: Remove raw footage entirely

## Tips

1. **Consistent SD card name**: Name your SD card consistently for reliable detection

2. **Shoot with structure**: Morning/midday/evening clips help AI make better selections

3. **Talk to camera**: The AI prioritizes segments with speech

4. **Background music**: Add music files to a `music/` folder for future background track support

5. **Review before upload**: Use `--skip-upload` to review the edit first

## Troubleshooting

**"SD card not found"**
- Check the volume name in `config.yaml` matches exactly
- Verify the footage path (DJI cameras use `DCIM/DJI_001`)

**"Transcription failed"**
- Ensure Whisper is installed: `pip install openai-whisper`
- Try a smaller model in config if memory issues

**"YouTube upload failed"**
- Verify `credentials.json` exists
- Delete `token.pickle` to re-authenticate

**"FFmpeg errors"**
- Install FFmpeg: `brew install ffmpeg`
- Check codec support: `ffmpeg -codecs | grep h264`
