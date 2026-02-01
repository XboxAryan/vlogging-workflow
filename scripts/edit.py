#!/usr/bin/env python3
"""
Video Editing Script
====================
Uses FFmpeg to cut and concatenate video segments based on AI analysis.
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from concurrent_utils import get_worker_count, is_concurrency_enabled

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_resolution_params(resolution):
    """Get FFmpeg scale parameters for target resolution"""
    resolutions = {
        'original': None,
        '4k': '3840:2160',
        '1080p': '1920:1080',
        '720p': '1280:720',
        '480p': '854:480'
    }
    return resolutions.get(resolution.lower(), '1920:1080')

def extract_segment(input_path, output_path, start, end, config):
    """Extract a segment from a video file"""
    duration = end - start
    codec = config['output'].get('codec', 'h264')
    hw_accel = config['output'].get('hw_accel', 'none')

    # Build FFmpeg command
    cmd = ['ffmpeg']

    # Add hardware acceleration input decoding if available
    if hw_accel == 'videotoolbox':
        cmd.extend(['-hwaccel', 'videotoolbox'])
    elif hw_accel == 'nvenc':
        cmd.extend(['-hwaccel', 'cuda'])

    cmd.extend([
        '-ss', str(start),
        '-i', str(input_path),
        '-t', str(duration),
    ])

    # Video encoding settings based on codec
    if codec in ('h264_videotoolbox', 'hevc_videotoolbox'):
        # macOS VideoToolbox GPU encoding
        cmd.extend([
            '-c:v', codec,
            '-q:v', '65',  # Quality 0-100, higher = better (65 is good balance)
            '-allow_sw', '1',  # Allow software fallback
        ])
    elif codec in ('h264_nvenc', 'hevc_nvenc'):
        # NVIDIA GPU encoding
        cmd.extend([
            '-c:v', codec,
            '-preset', 'p4',  # NVENC preset (p1=fastest, p7=slowest)
            '-cq', str(config['output']['crf']),
        ])
    else:
        # Software encoding (h264, libx264)
        cmd.extend([
            '-c:v', codec if codec != 'h264' else 'libx264',
            '-preset', config['output']['preset'],
            '-crf', str(config['output']['crf']),
        ])

    # Audio encoding
    cmd.extend(['-c:a', 'aac', '-b:a', '192k'])

    # Add scaling if not original
    scale = get_resolution_params(config['output']['resolution'])
    if scale:
        cmd.extend(['-vf', f'scale={scale}:force_original_aspect_ratio=decrease,pad={scale}:(ow-iw)/2:(oh-ih)/2'])

    # Set frame rate
    cmd.extend(['-r', str(config['output']['fps'])])

    # Output
    cmd.extend(['-y', str(output_path)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

def concatenate_segments(segment_paths, output_path, config):
    """Concatenate multiple video segments into one"""
    # Create concat file with absolute paths
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for path in segment_paths:
            # Use absolute path for FFmpeg concat
            abs_path = Path(path).resolve()
            f.write(f"file '{abs_path}'\n")
        concat_file = f.name

    try:
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            '-y',
            str(output_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    finally:
        os.unlink(concat_file)

def add_intro_outro(video_path, output_path, config):
    """Add intro and outro if they exist"""
    base_dir = Path(config['project']['base_dir'])
    intro_path = base_dir / "templates" / "intro.mp4"
    outro_path = base_dir / "templates" / "outro.mp4"

    segments = []

    if intro_path.exists():
        segments.append(str(intro_path))

    segments.append(str(video_path))

    if outro_path.exists():
        segments.append(str(outro_path))

    if len(segments) == 1:
        # No intro/outro, just copy
        import shutil
        shutil.copy(video_path, output_path)
        return True

    return concatenate_segments(segments, output_path, config)


def add_background_music(video_path, output_path, music_path, config, volume=0.15):
    """
    Mix background music into video using FFmpeg.

    Args:
        video_path: Input video file
        output_path: Output video file
        music_path: Background music file
        config: Configuration dictionary
        volume: Music volume (0.0-1.0)

    Returns:
        True if successful
    """
    music_config = config.get('music', {})
    fade_in = music_config.get('fade_in', 2)
    fade_out = music_config.get('fade_out', 2)

    # Get video duration for fade out calculation
    probe_cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        str(video_path)
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print("[red]Failed to probe video duration[/red]")
        return False

    video_info = json.loads(result.stdout)
    video_duration = float(video_info['format'].get('duration', 0))
    fade_out_start = max(0, video_duration - fade_out)

    # Build FFmpeg filter for music mixing
    # - Loop music if needed
    # - Apply volume
    # - Fade in at start
    # - Fade out at end
    # - Mix with original audio
    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{video_duration},"
        f"volume={volume},"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start}:d={fade_out}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    cmd = [
        'ffmpeg',
        '-i', str(video_path),
        '-i', str(music_path),
        '-filter_complex', audio_filter,
        '-map', '0:v',
        '-map', '[aout]',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-y',
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]FFmpeg error: {result.stderr[:500]}[/red]")
        return False

    return True


def burn_subtitles(video_path, output_path, srt_path, config):
    """
    Burn SRT subtitles into video using FFmpeg.

    Args:
        video_path: Input video file
        output_path: Output video file
        srt_path: SRT subtitle file
        config: Configuration dictionary

    Returns:
        True if successful
    """
    subtitle_config = config.get('subtitles', {})
    font_size = subtitle_config.get('font_size', 24)
    font_color = subtitle_config.get('font_color', 'white')
    outline_color = subtitle_config.get('outline_color', 'black')
    outline_width = subtitle_config.get('outline_width', 2)
    margin = subtitle_config.get('margin', 40)

    # Escape the SRT path for FFmpeg filter (handle special characters)
    srt_escaped = str(srt_path).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")

    # Build subtitle filter with styling
    subtitle_filter = (
        f"subtitles='{srt_escaped}':"
        f"force_style='FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"  # White in ABGR
        f"OutlineColour=&H00000000,"  # Black outline
        f"BorderStyle=1,"
        f"Outline={outline_width},"
        f"MarginV={margin}'"
    )

    codec = config['output'].get('codec', 'h264')

    cmd = ['ffmpeg', '-i', str(video_path), '-vf', subtitle_filter, '-c:a', 'copy']

    # Video encoding settings based on codec
    if codec in ('h264_videotoolbox', 'hevc_videotoolbox'):
        cmd.extend(['-c:v', codec, '-q:v', '65', '-allow_sw', '1'])
    elif codec in ('h264_nvenc', 'hevc_nvenc'):
        cmd.extend(['-c:v', codec, '-preset', 'p4', '-cq', str(config['output']['crf'])])
    else:
        cmd.extend([
            '-c:v', codec if codec != 'h264' else 'libx264',
            '-preset', config['output']['preset'],
            '-crf', str(config['output']['crf']),
        ])

    cmd.extend(['-y', str(output_path)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]FFmpeg subtitle error: {result.stderr[:500]}[/red]")
        return False

    return True

def process_project(project_path, config, music_path=None, music_volume=0.15, enable_subtitles=False):
    """
    Edit video based on analysis.

    Args:
        project_path: Path to project directory
        config: Configuration dictionary
        music_path: Optional path to background music file
        music_volume: Volume for background music (0.0-1.0)
        enable_subtitles: Whether to burn subtitles into video

    Returns:
        Path to final video
    """
    project_path = Path(project_path)
    raw_dir = project_path / "raw"
    output_dir = project_path / "output"

    # Load analysis
    analysis_path = project_path / "analysis.json"
    if not analysis_path.exists():
        console.print(f"[red]Error: No analysis.json found. Run analyze.py first.[/red]")
        return None

    with open(analysis_path, 'r') as f:
        analysis = json.load(f)

    # Load manifest for date and title
    manifest_path = project_path / "manifest.json"
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    project_date = manifest.get('date', datetime.now().strftime('%Y-%m-%d'))
    project_title = manifest.get('title', '')

    # Create output name base: {slug}_{date} or just {date}
    if project_title:
        import re
        # Slugify the title
        slug = project_title.lower()
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'[^a-z0-9-]', '', slug)
        slug = re.sub(r'-+', '-', slug).strip('-')
        output_name_base = f"{slug}_{project_date}" if slug else project_date
    else:
        output_name_base = project_date

    console.print(Panel.fit(
        f"[bold]Editing project: {project_date}[/bold]\n"
        f"Segments to process: {len(analysis.get('selected_segments', []))}",
        title="Video Editing"
    ))

    selected_segments = analysis.get('selected_segments', [])
    if not selected_segments:
        console.print("[red]Error: No segments selected for editing[/red]")
        return None

    # Sort segments by suggested order
    selected_segments.sort(key=lambda s: s.get('suggested_order', 0))

    # Create temp directory for segment files
    temp_dir = output_dir / "temp_segments"
    temp_dir.mkdir(exist_ok=True)

    # Prepare extraction tasks
    extraction_tasks = []
    for i, seg in enumerate(selected_segments):
        input_file = raw_dir / seg['file']
        if not input_file.exists():
            console.print(f"  [yellow]Warning: {seg['file']} not found, skipping[/yellow]")
            continue
        output_file = temp_dir / f"segment_{i:03d}.mp4"
        extraction_tasks.append({
            'index': i,
            'input_file': input_file,
            'output_file': output_file,
            'start': seg['start'],
            'end': seg['end'],
        })

    if not extraction_tasks:
        console.print("[red]Error: No valid segments to extract[/red]")
        return None

    def extract_task(task_info):
        """Extract a single segment - designed for parallel execution."""
        success = extract_segment(
            task_info['input_file'],
            task_info['output_file'],
            task_info['start'],
            task_info['end'],
            config
        )
        return {
            'index': task_info['index'],
            'output_file': task_info['output_file'],
            'success': success
        }

    # Parallel or sequential extraction based on config
    segment_results = [None] * len(extraction_tasks)
    max_workers = get_worker_count(config, 'ffmpeg')
    use_parallel = is_concurrency_enabled(config) and len(extraction_tasks) > 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        if use_parallel and max_workers > 1:
            # Parallel extraction
            task = progress.add_task(f"Extracting segments ({max_workers} workers)...", total=len(extraction_tasks))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(extract_task, t): t for t in extraction_tasks}

                for future in as_completed(futures):
                    result = future.result()
                    segment_results[result['index']] = result
                    progress.update(task, advance=1)
        else:
            # Sequential extraction (fallback)
            task = progress.add_task("Extracting segments...", total=len(extraction_tasks))

            for t in extraction_tasks:
                progress.update(task, description=f"Extracting segment {t['index']+1}/{len(selected_segments)}")
                result = extract_task(t)
                segment_results[result['index']] = result
                progress.update(task, advance=1)

    # Collect successful extractions in order
    segment_paths = []
    for result in segment_results:
        if result and result['success']:
            segment_paths.append(result['output_file'])
        elif result:
            console.print(f"  [yellow]Warning: Failed to extract segment {result['index']+1}[/yellow]")

    if not segment_paths:
        console.print("[red]Error: No segments were successfully extracted[/red]")
        return None

    # Concatenate all segments
    console.print("\n[bold]Concatenating segments...[/bold]")
    concat_output = output_dir / f"concat_{output_name_base}.mp4"

    if not concatenate_segments(segment_paths, concat_output, config):
        console.print("[red]Error: Failed to concatenate segments[/red]")
        return None

    console.print("[green]✓ Segments concatenated[/green]")

    # Add intro/outro
    console.print("[bold]Adding intro/outro...[/bold]")
    with_intro_outro = output_dir / f"with_intro_{output_name_base}.mp4"

    if not add_intro_outro(concat_output, with_intro_outro, config):
        console.print("[yellow]Warning: Failed to add intro/outro, using concatenated version[/yellow]")
        with_intro_outro = concat_output

    console.print("[green]✓ Intro/outro added[/green]")

    # Track current video file through processing chain
    current_video = with_intro_outro

    # Add background music if requested
    if music_path and Path(music_path).exists():
        console.print("[bold]Adding background music...[/bold]")
        with_music = output_dir / f"with_music_{output_name_base}.mp4"

        if add_background_music(current_video, with_music, music_path, config, volume=music_volume):
            console.print(f"[green]✓ Music added (volume: {music_volume})[/green]")
            current_video = with_music
        else:
            console.print("[yellow]Warning: Failed to add background music[/yellow]")
    elif music_path:
        console.print(f"[yellow]Warning: Music file not found: {music_path}[/yellow]")

    # Burn subtitles if requested
    if enable_subtitles:
        console.print("[bold]Generating and burning subtitles...[/bold]")

        # Generate SRT from transcript
        from subtitle_generator import generate_subtitles
        srt_path = generate_subtitles(project_path, use_analysis=True)

        if srt_path and srt_path.exists():
            with_subs = output_dir / f"with_subs_{output_name_base}.mp4"
            if burn_subtitles(current_video, with_subs, srt_path, config):
                console.print("[green]✓ Subtitles burned[/green]")
                current_video = with_subs
            else:
                console.print("[yellow]Warning: Failed to burn subtitles[/yellow]")
        else:
            console.print("[yellow]Warning: Failed to generate subtitles[/yellow]")

    # Rename to final output
    final_output = output_dir / f"vlog_{output_name_base}.mp4"
    if current_video != final_output:
        import shutil
        shutil.move(str(current_video), str(final_output))

    console.print("[green]✓ Final video created[/green]")

    # Get final video info
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', str(final_output)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        info = json.loads(result.stdout)
        duration = float(info['format'].get('duration', 0))
        size = int(info['format'].get('size', 0))

        console.print(f"\n[bold]Final video:[/bold]")
        console.print(f"  Duration: {duration/60:.1f} minutes")
        console.print(f"  Size: {size/1024/1024:.1f} MB")
        console.print(f"  Path: {final_output}")

    # Cleanup temp files
    console.print("\n[bold]Cleaning up temporary files...[/bold]")
    for seg_path in segment_paths:
        try:
            seg_path.unlink()
        except:
            pass
    try:
        temp_dir.rmdir()
    except:
        pass

    # Clean up intermediate processing files
    intermediate_patterns = [
        f"concat_{output_name_base}.mp4",
        f"with_intro_{output_name_base}.mp4",
        f"with_music_{output_name_base}.mp4",
        f"with_subs_{output_name_base}.mp4",
    ]
    for pattern in intermediate_patterns:
        intermediate_file = output_dir / pattern
        if intermediate_file.exists() and intermediate_file != final_output:
            try:
                intermediate_file.unlink()
            except:
                pass

    # Update manifest with output info
    manifest['output'] = {
        'path': str(final_output),
        'created_at': datetime.now().isoformat(),
        'duration': duration if 'duration' in dir() else 0,
        'size': size if 'size' in dir() else 0
    }
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    console.print(Panel.fit(
        f"[bold green]Editing complete![/bold green]\n"
        f"Output: {final_output}",
        title="Done"
    ))

    return final_output

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

    # Parse command line arguments
    music_path = None
    music_volume = config.get('music', {}).get('default_volume', 0.15)
    enable_subtitles = False

    # Check for --music flag
    if "--music" in sys.argv:
        music_idx = sys.argv.index("--music")
        if music_idx + 1 < len(sys.argv):
            music_path = sys.argv[music_idx + 1]

    # Check for --music-volume flag
    if "--music-volume" in sys.argv:
        vol_idx = sys.argv.index("--music-volume")
        if vol_idx + 1 < len(sys.argv):
            try:
                music_volume = float(sys.argv[vol_idx + 1])
            except ValueError:
                pass

    # Check for --subtitles flag
    enable_subtitles = "--subtitles" in sys.argv

    # Check for project path argument (exclude flags and their values)
    args = []
    skip_next = False
    for i, a in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if a.startswith('--'):
            if a in ("--music", "--music-volume"):
                skip_next = True
            continue
        args.append(a)

    if args:
        project_path = Path(args[0])
    else:
        project_path = find_latest_project(config)

    if not project_path or not project_path.exists():
        console.print("[red]Error: No project found. Run previous steps first.[/red]")
        return

    result = process_project(
        project_path,
        config,
        music_path=music_path,
        music_volume=music_volume,
        enable_subtitles=enable_subtitles,
    )

    if result:
        print(f"\nOUTPUT_VIDEO={result}")
        print(f"PROJECT_PATH={project_path}")

if __name__ == "__main__":
    main()
