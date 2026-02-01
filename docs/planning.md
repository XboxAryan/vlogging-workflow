# Video Creator - Implementation Plan

## Overview
Interactive terminal menu system for AI-assisted video editing with context support.

## Architecture

```
video_creator.py (Main Menu)
    |
    +-- session.py (State Management)
    +-- music_manager.py (Music Selection)
    +-- subtitle_generator.py (SRT Generation)
    |
    v
Existing Pipeline (Modified)
ingest -> transcribe -> analyze* -> edit* -> upload
                        (* = receives user context)
```

## User Context Data Structure

```json
{
  "title": "Morning Coffee & Code",
  "description": "A calm morning vlog about my coffee ritual",
  "instructions": "Keep it slow-paced, prioritize quiet moments",
  "tone": "calm",
  "music": {
    "enabled": true,
    "track": "music/lo-fi-beats.mp3",
    "volume": 0.15
  },
  "subtitles": {
    "enabled": true
  }
}
```

## Menu Flow

```
VIDEO CREATOR
├── [1] Create New Video
│   ├── Enter video title
│   ├── Enter description
│   ├── Enter AI editing instructions
│   ├── Select music (or none)
│   ├── Enable/disable subtitles
│   ├── Confirm and run pipeline
│   └── View progress
├── [2] Continue Previous Session
├── [3] Quick Pipeline (no context)
├── [4] Settings
└── [5] Exit
```

## Implementation Phases

- [x] Phase 1: Core Menu & Session Management
- [x] Phase 2: Enhanced AI Context
- [x] Phase 3: Music Integration
- [x] Phase 4: Subtitle Support
- [x] Phase 5: Polish

## Session Documentation Structure

```
docs/sessions/
└── 2026-01-17_morning-coffee/
    ├── session.json      # User inputs
    ├── progress.log      # Pipeline progress
    └── ai_prompt.md      # Actual prompt sent to AI
```
