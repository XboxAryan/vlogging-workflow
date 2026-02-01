#!/usr/bin/env python3
"""
Transcription Script
====================
Extracts audio from video files and transcribes using Whisper.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel

from concurrent_utils import get_worker_count, is_concurrency_enabled

console = Console()

# Global model cache to avoid reloading
_whisper_model = None
_whisper_model_size = None

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def extract_audio(video_path, output_path):
    """Extract audio from video file using FFmpeg"""
    cmd = [
        'ffmpeg',
        '-i', str(video_path),
        '-vn',  # No video
        '-acodec', 'pcm_s16le',  # PCM format for Whisper
        '-ar', '16000',  # 16kHz sample rate
        '-ac', '1',  # Mono
        '-y',  # Overwrite
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

def get_whisper_model(model_size):
    """Get or load Whisper model (cached to avoid reloading)."""
    global _whisper_model, _whisper_model_size

    try:
        import whisper
    except ImportError:
        console.print("[red]Error: whisper not installed. Run: pip install openai-whisper[/red]")
        return None

    # Return cached model if same size
    if _whisper_model is not None and _whisper_model_size == model_size:
        return _whisper_model

    console.print(f"[bold]Loading Whisper model ({model_size})...[/bold]")
    _whisper_model = whisper.load_model(model_size)
    _whisper_model_size = model_size
    console.print("[green]✓ Model loaded[/green]")

    return _whisper_model


def transcribe_audio(audio_path, config, model=None):
    """Transcribe audio using Whisper.

    Args:
        audio_path: Path to audio file
        config: Configuration dictionary
        model: Pre-loaded Whisper model (optional, will load if not provided)

    Returns:
        Transcription result dictionary
    """
    model_size = config['transcription']['model_size']
    language = config['transcription']['language']

    # Use provided model or get/load from cache
    if model is None:
        model = get_whisper_model(model_size)
        if model is None:
            return None

    options = {
        'verbose': False,
        'word_timestamps': True
    }

    if language != 'auto':
        options['language'] = language

    result = model.transcribe(str(audio_path), **options)

    return result

def format_timestamp(seconds):
    """Format seconds to HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

def process_project(project_path, config):
    """
    Process all videos in a project directory.

    Args:
        project_path: Path to project directory
        config: Configuration dictionary

    Returns:
        Combined transcript data
    """
    project_path = Path(project_path)
    raw_dir = project_path / "raw"
    audio_dir = project_path / "audio"
    transcript_dir = project_path / "transcripts"

    # Load manifest
    manifest_path = project_path / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]Error: No manifest.json found in {project_path}[/red]")
        return None

    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    console.print(Panel.fit(
        f"[bold]Processing project: {manifest['date']}[/bold]\n"
        f"Files: {len(manifest['files'])}",
        title="Transcription"
    ))

    # Pre-load Whisper model ONCE for all files
    model_size = config['transcription']['model_size']
    whisper_model = get_whisper_model(model_size)
    if whisper_model is None:
        return None

    all_segments = []
    file_transcripts = []

    # Prepare file processing info
    files_to_process = []
    for file_info in manifest['files']:
        filename = file_info['name']
        video_path = raw_dir / filename
        audio_filename = Path(filename).stem + ".wav"
        audio_path = audio_dir / audio_filename
        transcript_filename = Path(filename).stem + ".json"
        transcript_path = transcript_dir / transcript_filename

        files_to_process.append({
            'file_info': file_info,
            'filename': filename,
            'video_path': video_path,
            'audio_path': audio_path,
            'transcript_path': transcript_path,
        })

    # Identify files needing audio extraction
    files_needing_audio = [
        f for f in files_to_process
        if not f['transcript_path'].exists() and not f['audio_path'].exists()
    ]

    # Parallel audio extraction
    if files_needing_audio:
        max_workers = get_worker_count(config, 'ffmpeg')
        use_parallel = is_concurrency_enabled(config) and len(files_needing_audio) > 1

        def extract_audio_task(f):
            """Extract audio from a single video."""
            success = extract_audio(f['video_path'], f['audio_path'])
            return {'filename': f['filename'], 'audio_path': f['audio_path'], 'success': success}

        console.print(f"\n[bold]Extracting audio from {len(files_needing_audio)} file(s)...[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=True
        ) as progress:
            if use_parallel and max_workers > 1:
                task = progress.add_task(f"Extracting audio ({max_workers} workers)...", total=len(files_needing_audio))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = list(executor.map(extract_audio_task, files_needing_audio))
                    for result in futures:
                        if not result['success']:
                            console.print(f"  [yellow]Warning: Failed to extract audio for {result['filename']}[/yellow]")
                        progress.update(task, advance=1)
            else:
                task = progress.add_task("Extracting audio...", total=len(files_needing_audio))
                for f in files_needing_audio:
                    result = extract_audio_task(f)
                    if not result['success']:
                        console.print(f"  [yellow]Warning: Failed to extract audio for {result['filename']}[/yellow]")
                    progress.update(task, advance=1)

        console.print("[green]✓ Audio extraction complete[/green]")

    # Process each file (transcription is sequential due to shared model)
    console.print(f"\n[bold]Transcribing {len(files_to_process)} file(s)...[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Transcribing...", total=len(files_to_process))

        for f in files_to_process:
            filename = f['filename']
            audio_path = f['audio_path']
            transcript_path = f['transcript_path']
            file_info = f['file_info']

            progress.update(task, description=f"Processing {filename}")

            # Check if transcript already exists
            if transcript_path.exists():
                with open(transcript_path, 'r') as fp:
                    transcript = json.load(fp)
            else:
                # Check if audio was extracted
                if not audio_path.exists():
                    console.print(f"  [yellow]Skipping {filename} - audio extraction failed[/yellow]")
                    progress.update(task, advance=1)
                    continue

                # Transcribe using pre-loaded model
                result = transcribe_audio(audio_path, config, model=whisper_model)
                if result is None:
                    progress.update(task, advance=1)
                    continue

                # Build transcript structure
                transcript = {
                    'file': filename,
                    'language': result.get('language', 'unknown'),
                    'duration': file_info['duration'],
                    'text': result['text'],
                    'segments': []
                }

                for segment in result['segments']:
                    seg_data = {
                        'start': segment['start'],
                        'end': segment['end'],
                        'text': segment['text'].strip(),
                        'words': []
                    }

                    # Include word-level timestamps if available
                    if 'words' in segment:
                        for word in segment['words']:
                            seg_data['words'].append({
                                'word': word['word'],
                                'start': word['start'],
                                'end': word['end']
                            })

                    transcript['segments'].append(seg_data)

                # Save individual transcript
                with open(transcript_path, 'w') as fp:
                    json.dump(transcript, fp, indent=2)

            file_transcripts.append(transcript)

            # Add segments with file reference
            for seg in transcript['segments']:
                all_segments.append({
                    'file': filename,
                    'start': seg['start'],
                    'end': seg['end'],
                    'text': seg['text']
                })

            progress.update(task, advance=1)

    # Create combined transcript
    combined = {
        'project_date': manifest['date'],
        'processed_at': datetime.now().isoformat(),
        'total_files': len(file_transcripts),
        'files': file_transcripts,
        'all_segments': all_segments,
        'full_text': ' '.join(t['text'] for t in file_transcripts)
    }

    # Save combined transcript
    combined_path = transcript_dir / "combined.json"
    with open(combined_path, 'w') as f:
        json.dump(combined, f, indent=2)

    # Also save a readable text version
    text_path = transcript_dir / "transcript.txt"
    with open(text_path, 'w') as f:
        f.write(f"Transcript - {manifest['date']}\n")
        f.write("=" * 50 + "\n\n")

        for transcript in file_transcripts:
            f.write(f"\n--- {transcript['file']} ---\n\n")
            for seg in transcript['segments']:
                timestamp = format_timestamp(seg['start'])
                f.write(f"[{timestamp}] {seg['text']}\n")

    console.print(Panel.fit(
        f"[bold green]Transcription complete![/bold green]\n"
        f"Output: {transcript_dir}",
        title="Done"
    ))

    return combined

def find_latest_project(config):
    """Find the most recent project directory"""
    base_dir = Path(config['project']['base_dir'])
    projects_dir = base_dir / "projects"

    if not projects_dir.exists():
        return None

    projects = sorted(projects_dir.iterdir(), reverse=True)
    if projects:
        return projects[0]
    return None

def main():
    """Main entry point"""
    config = load_config()

    # Check for project path argument
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        project_path = Path(sys.argv[1])
    else:
        # Find latest project
        project_path = find_latest_project(config)

    if not project_path or not project_path.exists():
        console.print("[red]Error: No project found. Run ingest.py first.[/red]")
        return

    result = process_project(project_path, config)

    if result:
        print(f"\nPROJECT_PATH={project_path}")

if __name__ == "__main__":
    main()
