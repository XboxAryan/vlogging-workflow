# Vlogging Workflow

Automated daily vlog pipeline for DJI Osmo Pocket 3. Transforms raw 4K footage into edited 8-10 minute videos uploaded to YouTube.

## Overview

```
SD Card → Ingest → Transcribe → AI Analysis → Edit → Upload → Cleanup
```

**Two workflow modes:**
- **Full Pipeline**: AI-assisted editing from raw footage to final video
- **Direct Upload**: Upload existing videos directly to YouTube with metadata

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.11+
- FFmpeg (`brew install ffmpeg`)
- OpenRouter/Anthropic API key (for AI analysis)
- Google Cloud credentials (for YouTube upload)

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/XboxAryan/vlogging-workflow.git
cd vlogging-workflow

# Using uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
```

Key settings to update:
- `sd_card.volume_name`: Your SD card's name in Finder
- `project.base_dir`: Path to this project
- `ai.provider`: Your AI provider (openrouter, anthropic, openai)

### 3. Set up API keys

```bash
# Create .env file
cp .env.example .env

# Add your API key
echo "OPENROUTER_API_KEY=your-key-here" >> .env
# Or for Anthropic
echo "ANTHROPIC_API_KEY=your-key-here" >> .env
```

### 4. Set up YouTube API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable **YouTube Data API v3**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose "External" user type
   - Fill in app name and email
   - Add yourself as a **Test user** (required while app is in testing mode)
5. Go to **APIs & Services → Credentials**
   - Create **OAuth 2.0 Client ID** (Desktop application)
   - Download as `credentials.json` and place in project root
6. **Create a YouTube channel** if you don't have one:
   - Go to [youtube.com](https://youtube.com)
   - Click your profile → Create a channel

> **Note**: Custom thumbnails require a [verified YouTube account](https://support.google.com/youtube/answer/171664). Unverified accounts can still upload videos but must set thumbnails manually in YouTube Studio.

### 5. Run

```bash
# Interactive menu (recommended)
python scripts/video_creator.py

# Or full automatic pipeline
python scripts/pipeline.py
```

## Usage Modes

### Interactive Mode (Recommended)

The interactive menu provides the best experience:

```bash
python scripts/video_creator.py
```

**Menu options:**
- **Create New Video**: Full AI-assisted pipeline with custom context
- **Upload to YouTube**: Direct upload from SD card with metadata
- **Continue Previous Session**: Resume interrupted workflows
- **Quick Pipeline**: Run without custom context
- **View Session History**: See past sessions
- **Settings**: View current configuration

### YouTube Direct Upload

Upload videos directly from your SD card without going through the full editing pipeline:

1. Select **Upload to YouTube** from the menu
2. Choose videos from your SD card
3. For each video:
   - Select a thumbnail from generated frames
   - Enter title and description
   - Add custom tags
   - Choose privacy status
4. Review the upload queue
5. Confirm and upload

Upload results are saved to `logs/upload_history.json`.

### Full Pipeline Mode

Run the complete automated workflow:

```bash
python scripts/pipeline.py
```

Or run steps individually:

```bash
python scripts/ingest.py          # Import from SD card
python scripts/transcribe.py      # Transcribe audio
python scripts/analyze.py         # AI highlight selection
python scripts/edit.py            # Create final video
python scripts/upload.py          # Upload to YouTube
```

### Watch Mode

Auto-trigger when SD card is inserted:

```bash
python scripts/watch_sd.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `video_creator.py` | Interactive menu for all workflows |
| `pipeline.py` | Run complete automated pipeline |
| `watch_sd.py` | Monitor for SD card and auto-trigger |
| `ingest.py` | Copy footage from SD card |
| `transcribe.py` | Transcribe audio using Whisper |
| `analyze.py` | AI-powered highlight selection |
| `edit.py` | FFmpeg video editing |
| `upload.py` | YouTube upload |
| `cleanup.py` | Archive/delete raw footage |

## Configuration

Edit `config.yaml` to customize:

- **SD card**: Volume name, footage path
- **Output**: Resolution, quality, codec
- **YouTube**: Privacy, title template, default tags
- **YouTube Direct**: Default tags, category, privacy for direct uploads
- **AI**: Provider, model, highlight ratio
- **Cleanup**: Archive vs delete, retention period
- **Music**: Background music library path, volume
- **Subtitles**: Font, position, colors

## Project Structure

```
vlogging-workflow/
├── scripts/           # Python scripts
├── projects/          # Per-day project folders (gitignored)
├── templates/         # Intro/outro videos
├── music/             # Background music library
├── logs/              # Upload history and logs
├── config.yaml        # Your configuration (gitignored)
├── config.example.yaml # Template configuration
├── credentials.json   # YouTube OAuth (gitignored)
└── token.pickle       # YouTube auth token (gitignored)
```

After running, each day's project:

```
projects/2024-01-15/
├── raw/              # Original 4K footage
├── audio/            # Extracted audio
├── transcripts/      # Whisper output
├── output/           # Final video
├── manifest.json     # Project metadata
└── analysis.json     # AI selections
```

## Troubleshooting

### SD Card Issues

**"SD card not found"**
- Check `config.yaml` volume name matches exactly (case-sensitive)
- Verify the footage path (DJI cameras use `DCIM/DJI_001`)

### YouTube Issues

**"access_denied" or "Unauthorized"**
- Add yourself as a Test user in Google Cloud Console
- Ensure you have a YouTube channel created
- Delete `token.pickle` and re-authenticate

**"youtubeSignupRequired"**
- You need to create a YouTube channel first
- Go to youtube.com → Profile → Create channel

**Custom thumbnail not setting**
- Verify your YouTube account at [youtube.com/verify](https://youtube.com/verify)
- Unverified accounts can't set thumbnails via API

**Upload is slow**
- Upload speed depends on your internet connection
- The code uses 50MB chunks for efficiency

### Transcription Issues

**"Transcription failed"**
- Ensure Whisper is installed: `pip install openai-whisper`
- Try a smaller model in config if memory issues

### FFmpeg Issues

**"FFmpeg errors"**
- Install FFmpeg: `brew install ffmpeg`
- Check codec support: `ffmpeg -codecs | grep h264`

## Tips

1. **Consistent SD card name**: Name your SD card consistently for reliable detection

2. **Shoot with structure**: Morning/midday/evening clips help AI make better selections

3. **Talk to camera**: The AI prioritizes segments with speech

4. **Review before upload**: Use `--skip-upload` to review the edit first

5. **Background music**: Add music files to `music/` folder

6. **Intro/Outro**: Place `intro.mp4` and `outro.mp4` in `templates/`

## License

MIT License - see [LICENSE](LICENSE) for details.
