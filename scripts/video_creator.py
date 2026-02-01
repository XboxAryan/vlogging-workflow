#!/usr/bin/env python3
"""
Video Creator
=============
Interactive terminal menu for AI-assisted video creation.
Provides context to the AI for better editing decisions.
"""

import os
import sys
import subprocess
import base64
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file if it exists
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

import yaml
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from session import (
    SessionManager, UserContext, MusicConfig, SubtitleConfig, Session,
    YouTubeUploadItem, YouTubeUploadResult
)
from music_manager import MusicManager
from footage_selector import (
    FootageSelector, DayGroup, VideoFile, scan_sd_card,
    generate_thumbnails, get_thumbnail_paths, preview_video
)
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

# Upload queue state file for resume functionality
UPLOAD_QUEUE_FILE = ".upload_queue.json"


def get_upload_queue_path(config: dict) -> Path:
    """Get path to the upload queue state file."""
    base_dir = Path(config['project']['base_dir'])
    return base_dir / UPLOAD_QUEUE_FILE


def save_upload_queue(items: list[YouTubeUploadItem], config: dict) -> Path:
    """
    Save upload queue to disk for resume capability.

    Args:
        items: List of YouTubeUploadItem objects to save
        config: Configuration dictionary

    Returns:
        Path to the saved queue file
    """
    import json

    queue_path = get_upload_queue_path(config)
    data = {
        "created_at": datetime.now().isoformat(),
        "items": [item.to_dict() for item in items],
    }
    with open(queue_path, 'w') as f:
        json.dump(data, f, indent=2)
    return queue_path


def load_upload_queue(config: dict) -> list[YouTubeUploadItem] | None:
    """
    Load pending upload queue from disk.

    Args:
        config: Configuration dictionary

    Returns:
        List of YouTubeUploadItem objects or None if no queue exists
    """
    import json

    queue_path = get_upload_queue_path(config)
    if not queue_path.exists():
        return None

    try:
        with open(queue_path, 'r') as f:
            data = json.load(f)
        items = [YouTubeUploadItem.from_dict(item) for item in data.get('items', [])]
        return items if items else None
    except (json.JSONDecodeError, IOError, KeyError):
        return None


def clear_upload_queue(config: dict) -> None:
    """Remove the upload queue file after successful completion."""
    queue_path = get_upload_queue_path(config)
    if queue_path.exists():
        queue_path.unlink()


def update_upload_queue(remaining_items: list[YouTubeUploadItem], config: dict) -> None:
    """Update the queue file with remaining items after each successful upload."""
    if remaining_items:
        save_upload_queue(remaining_items, config)
    else:
        clear_upload_queue(config)


def detect_terminal_image_support() -> str:
    """
    Detect if the terminal supports inline images.

    Returns:
        'iterm2', 'kitty', or 'none'
    """
    # Check for iTerm2
    term_program = os.environ.get('TERM_PROGRAM', '')
    if term_program == 'iTerm.app':
        return 'iterm2'

    # Check for Ghostty (uses Kitty graphics protocol)
    if term_program == 'ghostty':
        return 'kitty'

    # Check for Kitty
    if os.environ.get('KITTY_WINDOW_ID'):
        return 'kitty'

    # Check LC_TERMINAL for iTerm2 (sometimes set differently)
    if os.environ.get('LC_TERMINAL', '') == 'iTerm2':
        return 'iterm2'

    return 'none'


def display_inline_image(image_path: Path, width: int = 50) -> bool:
    """
    Display an image inline in the terminal if supported.

    Args:
        image_path: Path to the image file
        width: Width in pixels (for iTerm2) or cells (for Kitty)

    Returns:
        True if image was displayed, False otherwise
    """
    if not image_path.exists():
        return False

    terminal = detect_terminal_image_support()

    if terminal == 'iterm2':
        try:
            with open(image_path, 'rb') as f:
                encoded = base64.b64encode(f.read()).decode('ascii')
            # iTerm2 inline image protocol
            # \033]1337;File=inline=1;width=Npx:<base64>\007
            sys.stdout.write(f"\033]1337;File=inline=1;width={width}px:{encoded}\007")
            sys.stdout.flush()
            return True
        except Exception:
            return False

    elif terminal == 'kitty':
        return display_kitty_image(image_path)

    return False


def display_kitty_image(image_path: Path) -> bool:
    """
    Display an image using the Kitty graphics protocol.
    Works with Kitty terminal and Ghostty.
    """
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()

        encoded = base64.standard_b64encode(image_data)
        CHUNK_SIZE = 4096

        first_chunk = True
        offset = 0

        while offset < len(encoded):
            chunk = encoded[offset:offset + CHUNK_SIZE]
            offset += CHUNK_SIZE
            is_last = offset >= len(encoded)
            m_value = 0 if is_last else 1

            if first_chunk:
                # a=T: transmit and display, f=100: PNG format
                control = f"a=T,f=100,m={m_value}"
                first_chunk = False
            else:
                control = f"m={m_value}"

            sys.stdout.buffer.write(b'\033_G')
            sys.stdout.buffer.write(control.encode('ascii'))
            sys.stdout.buffer.write(b';')
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.write(b'\033\\')

        sys.stdout.buffer.flush()
        return True
    except Exception:
        return False


def display_thumbnails_row(thumb_paths: list[Path]) -> bool:
    """
    Display a row of thumbnail images inline.

    Args:
        thumb_paths: List of paths to thumbnail images

    Returns:
        True if thumbnails were displayed, False otherwise
    """
    terminal = detect_terminal_image_support()

    if terminal == 'none':
        return False

    displayed = False
    for thumb_path in thumb_paths:
        if display_inline_image(thumb_path, width=80):
            sys.stdout.write(" ")  # Space between thumbnails
            displayed = True

    if displayed:
        sys.stdout.write("\n")
        sys.stdout.flush()

    return displayed


# Custom style for questionary
custom_style = Style([
    ('qmark', 'fg:cyan bold'),
    ('question', 'fg:white bold'),
    ('answer', 'fg:green bold'),
    ('pointer', 'fg:cyan bold'),
    ('highlighted', 'fg:cyan bold'),
    ('selected', 'fg:green'),
    ('separator', 'fg:gray'),
    ('instruction', 'fg:gray'),
    ('text', 'fg:white'),
])


def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def show_banner():
    """Display the Video Creator banner."""
    banner = """
╔═══════════════════════════════════════════════════════════════╗
║                     VIDEO CREATOR                              ║
║          AI-Assisted Video Editing with Context                ║
║                   [ESC] Return to Home                         ║
╚═══════════════════════════════════════════════════════════════╝
"""
    console.print(banner, style="cyan")


def get_tone_choices():
    """Get available tone choices for video."""
    return [
        questionary.Choice("General - No specific tone", value="general"),
        questionary.Choice("Calm - Relaxed, peaceful pace", value="calm"),
        questionary.Choice("Energetic - Fast-paced, exciting", value="energetic"),
        questionary.Choice("Informative - Educational, clear", value="informative"),
        questionary.Choice("Funny - Comedic, light-hearted", value="funny"),
        questionary.Choice("Cinematic - Dramatic, story-driven", value="cinematic"),
    ]


def collect_user_context(config, session_manager: SessionManager) -> UserContext:
    """Collect video context from user through interactive prompts."""
    console.print("\n[bold cyan]Let's set up your video context[/bold cyan]\n")

    # Title
    title = questionary.text(
        "Video title:",
        instruction="(Brief, descriptive title for this vlog)",
        style=custom_style,
    ).ask()

    if title is None:
        return None

    # Description
    description = questionary.text(
        "Video description:",
        instruction="(What is this video about? 1-2 sentences)",
        style=custom_style,
    ).ask()

    if description is None:
        return None

    # AI Instructions
    console.print("\n[dim]Give the AI specific instructions for editing.[/dim]")
    console.print("[dim]Examples: 'Focus on cooking scenes', 'Keep morning routine clips'[/dim]\n")

    instructions = questionary.text(
        "AI editing instructions:",
        instruction="(What should the AI prioritize or avoid?)",
        style=custom_style,
    ).ask()

    if instructions is None:
        return None

    # Tone
    tone = questionary.select(
        "Video tone/mood:",
        choices=get_tone_choices(),
        style=custom_style,
    ).ask()

    if tone is None:
        return None

    # Music selection
    music_config = select_music(config)
    if music_config is None:
        return None

    # Subtitles
    subtitles_enabled = questionary.confirm(
        "Enable subtitles?",
        default=False,
        style=custom_style,
    ).ask()

    if subtitles_enabled is None:
        return None

    return UserContext(
        title=title,
        description=description,
        instructions=instructions,
        tone=tone,
        music=music_config,
        subtitles=SubtitleConfig(enabled=subtitles_enabled),
    )


def select_music(config) -> MusicConfig | None:
    """Interactive music selection. Returns None if ESC pressed."""
    base_dir = Path(config['project']['base_dir'])
    music_path = base_dir / config.get('music', {}).get('library_path', 'music')

    music_manager = MusicManager(music_path)
    tracks = music_manager.list_tracks()

    choices = [questionary.Choice("No background music", value="none")]

    for track in tracks:
        display = music_manager.format_track_choice(track)
        choices.append(questionary.Choice(display, value=track.path))

    if len(tracks) == 0:
        console.print(f"\n[yellow]No music files found in {music_path}[/yellow]")
        console.print("[dim]Add .mp3, .m4a, .wav, or .flac files to the music/ folder[/dim]\n")

    selected = questionary.select(
        "Select background music:",
        choices=choices,
        style=custom_style,
    ).ask()

    # ESC pressed - return None to go back to home
    if selected is None:
        return None

    # "No background music" selected
    if selected == "none":
        return MusicConfig(enabled=False)

    if selected:
        # Ask for volume
        default_volume = config.get('music', {}).get('default_volume', 0.15)
        volume_str = questionary.text(
            "Music volume (0.0-1.0):",
            default=str(default_volume),
            style=custom_style,
        ).ask()

        # ESC pressed on volume prompt
        if volume_str is None:
            return None

        try:
            volume = float(volume_str) if volume_str else default_volume
            volume = max(0.0, min(1.0, volume))
        except ValueError:
            volume = default_volume

        return MusicConfig(enabled=True, track=selected, volume=volume)

    return MusicConfig(enabled=False)


def show_context_summary(context: UserContext, selected_files: list = None):
    """Display a summary of the collected context."""
    console.print("\n")

    files_info = ""
    if selected_files:
        total_duration = sum(f.duration or 0 for f in selected_files)
        minutes = int(total_duration // 60)
        files_info = f"\n[bold]Source Files:[/bold] {len(selected_files)} files ({minutes} min total)"

    console.print(Panel.fit(
        f"[bold]Title:[/bold] {context.title}\n"
        f"[bold]Description:[/bold] {context.description}\n"
        f"[bold]Instructions:[/bold] {context.instructions}\n"
        f"[bold]Tone:[/bold] {context.tone}\n"
        f"[bold]Music:[/bold] {'Enabled - ' + Path(context.music.track).name if context.music.enabled else 'Disabled'}\n"
        f"[bold]Subtitles:[/bold] {'Enabled' if context.subtitles.enabled else 'Disabled'}"
        f"{files_info}",
        title="Video Context Summary",
        border_style="cyan",
    ))


def select_footage(config) -> tuple[list[VideoFile], str]:
    """
    Interactive footage selection from SD card.

    Returns:
        Tuple of (list of selected VideoFile objects, date string)
    """
    console.print("\n[bold cyan]Scanning for footage...[/bold cyan]\n")

    sd_path, day_groups = scan_sd_card(config)

    if sd_path is None:
        console.print("[red]SD card not found![/red]")
        console.print(f"[dim]Looking for volume: {config.get('sd_card', {}).get('volume_name', 'Untitled')}[/dim]")
        console.print("[dim]Make sure your SD card is mounted.[/dim]\n")

        # Offer to use existing project footage instead
        use_existing = questionary.confirm(
            "Would you like to use footage from an existing project instead?",
            default=False,
            style=custom_style,
        ).ask()

        if use_existing:
            return select_existing_project_footage(config)
        return None, None

    if not day_groups:
        console.print("[yellow]No video files found on SD card.[/yellow]")
        return None, None

    selector = FootageSelector(config)

    # Show summary
    total_files = sum(g.file_count for g in day_groups)
    total_duration = sum(g.total_duration for g in day_groups)
    console.print(f"[green]Found {total_files} videos across {len(day_groups)} days[/green]")
    console.print(f"[dim]Total duration: {int(total_duration // 3600)}h {int((total_duration % 3600) // 60)}m[/dim]\n")

    # Select day
    day_choices = []
    for group in day_groups:
        day_choices.append(questionary.Choice(
            selector.format_day_choice(group),
            value=group.date
        ))
    day_choices.append(questionary.Choice("Cancel", value=None))

    selected_date = questionary.select(
        "Select footage date:",
        choices=day_choices,
        style=custom_style,
    ).ask()

    if selected_date is None:
        return None, None

    # Find the selected day group
    selected_group = next(g for g in day_groups if g.date == selected_date)

    # Ask if user wants all files or specific selection
    selection_mode = questionary.select(
        f"Import options for {selected_date}:",
        choices=[
            questionary.Choice(f"All {selected_group.file_count} files ({selected_group.total_duration_str})", value="all"),
            questionary.Choice("Select specific files", value="select"),
            questionary.Choice("Cancel", value=None),
        ],
        style=custom_style,
    ).ask()

    if selection_mode is None:
        return None, None

    if selection_mode == "all":
        return selected_group.files, selected_date

    # Let user select specific files with preview capability
    selected_files = select_files_with_preview(selected_group.files, selector)

    if not selected_files:
        console.print("[yellow]No files selected.[/yellow]")
        return None, None

    return selected_files, selected_date


def select_existing_project_footage(config) -> tuple[list[VideoFile], str]:
    """Select footage from an existing project directory."""
    base_dir = Path(config['project']['base_dir'])
    projects_dir = base_dir / "projects"

    if not projects_dir.exists():
        console.print("[yellow]No existing projects found.[/yellow]")
        return None, None

    # List existing projects
    project_dirs = sorted([d for d in projects_dir.iterdir() if d.is_dir()], reverse=True)

    if not project_dirs:
        console.print("[yellow]No existing projects found.[/yellow]")
        return None, None

    choices = []
    for proj_dir in project_dirs[:10]:
        raw_dir = proj_dir / "raw"
        if raw_dir.exists():
            video_count = len(list(raw_dir.glob("*.MP4")) + list(raw_dir.glob("*.mp4")))
            choices.append(questionary.Choice(
                f"{proj_dir.name} ({video_count} files)",
                value=proj_dir
            ))

    if not choices:
        console.print("[yellow]No projects with footage found.[/yellow]")
        return None, None

    choices.append(questionary.Choice("Cancel", value=None))

    selected_project = questionary.select(
        "Select existing project:",
        choices=choices,
        style=custom_style,
    ).ask()

    if selected_project is None:
        return None, None

    # Scan the raw directory
    raw_dir = selected_project / "raw"
    selector = FootageSelector(config)
    videos = selector.scan_directory(raw_dir)

    if not videos:
        console.print("[yellow]No videos found in project.[/yellow]")
        return None, None

    return videos, selected_project.name


def run_pipeline_step(script_name: str, args: list = None, description: str = None) -> bool:
    """Run a single pipeline step."""
    scripts_dir = Path(__file__).parent
    script_path = scripts_dir / script_name

    if description:
        console.print(f"\n[bold blue]>>> {description}[/bold blue]")

    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    result = subprocess.run(cmd)
    return result.returncode == 0


def run_full_pipeline(session: Session, session_manager: SessionManager, config: dict) -> bool:
    """Run the complete video creation pipeline with user context."""
    context = session.user_context
    import json

    console.print(Panel.fit(
        "[bold]Starting Video Creation Pipeline[/bold]\n\n"
        "Steps:\n"
        "1. Ingest selected footage\n"
        "2. Transcribe audio using Whisper\n"
        "3. Analyze and select highlights with AI\n"
        "4. Edit video with FFmpeg\n"
        "5. Add music and subtitles (if enabled)",
        title="Pipeline",
        border_style="cyan",
    ))

    start_time = datetime.now()

    # Step 1: Ingest - pass selected files if available
    session_manager.update_session(session, step="ingest")

    ingest_args = []
    if context.source_files:
        # Write file list to temp file for ingest.py
        base_dir = Path(config['project']['base_dir'])
        file_list_path = base_dir / ".ingest_files.json"
        with open(file_list_path, 'w') as f:
            json.dump({
                "files": context.source_files,
                "date": context.source_date,
                "title": context.title,  # Pass title for project directory naming
            }, f)
        ingest_args = ["--file-list", str(file_list_path)]

    if not run_pipeline_step("ingest.py", args=ingest_args, description="Ingesting footage"):
        session_manager.update_session(session, error="Ingestion failed")
        return False

    # Get the project path from the latest project
    base_dir = Path(config['project']['base_dir'])
    projects_dir = base_dir / "projects"
    project_dirs = sorted(projects_dir.iterdir(), reverse=True)
    if not project_dirs:
        session_manager.update_session(session, error="No project found after ingest")
        return False

    project_path = project_dirs[0]
    session_manager.update_session(session, project_path=str(project_path))

    # Step 2: Transcribe
    session_manager.update_session(session, step="transcribe")
    if not run_pipeline_step("transcribe.py", args=[str(project_path)], description="Transcribing audio"):
        session_manager.update_session(session, error="Transcription failed")
        return False

    # Step 3: Analyze (with user context)
    session_manager.update_session(session, step="analyze")

    # Save user context for analyze.py to read
    context_path = project_path / "user_context.json"
    import json
    with open(context_path, 'w') as f:
        json.dump(context.to_dict(), f, indent=2)

    if not run_pipeline_step(
        "analyze.py",
        args=[str(project_path), "--context", str(context_path)],
        description="Analyzing content with AI"
    ):
        session_manager.update_session(session, error="Analysis failed")
        return False

    # Step 4: Edit
    session_manager.update_session(session, step="edit")

    edit_args = [str(project_path)]
    if context.music.enabled and context.music.track:
        edit_args.extend(["--music", context.music.track])
        edit_args.extend(["--music-volume", str(context.music.volume)])
    if context.subtitles.enabled:
        edit_args.append("--subtitles")

    if not run_pipeline_step("edit.py", args=edit_args, description="Editing video"):
        session_manager.update_session(session, error="Editing failed")
        return False

    # Find output video
    output_dir = project_path / "output"
    video_files = list(output_dir.glob("vlog_*.mp4"))
    if video_files:
        output_video = str(video_files[0])
        session_manager.update_session(
            session,
            step="done",
            status="completed",
            output_video=output_video,
        )

    elapsed = datetime.now() - start_time

    console.print(Panel.fit(
        f"[bold green]Pipeline Complete![/bold green]\n\n"
        f"Total time: {elapsed.total_seconds()/60:.1f} minutes\n"
        f"Output: {output_video if video_files else 'Unknown'}",
        title="Done",
        border_style="green",
    ))

    return True


def create_new_video(config, session_manager: SessionManager):
    """Create a new video with full context."""
    clear_screen()
    show_banner()

    # Step 1: Select footage first
    console.print("[bold]Step 1: Select Source Footage[/bold]")
    selected_files, source_date = select_footage(config)

    if selected_files is None:
        console.print("[yellow]Cancelled[/yellow]")
        questionary.press_any_key_to_continue(style=custom_style).ask()
        return

    console.print(f"\n[green]Selected {len(selected_files)} files from {source_date}[/green]")

    # Step 2: Collect user context
    console.print("\n[bold]Step 2: Video Context[/bold]")
    context = collect_user_context(config, session_manager)
    if context is None:
        console.print("[yellow]Cancelled[/yellow]")
        return

    # Add source files to context
    context.source_files = [str(f.path) for f in selected_files]
    context.source_date = source_date

    # Show summary
    show_context_summary(context, selected_files)

    # Confirm
    proceed = questionary.confirm(
        "\nProceed with video creation?",
        default=True,
        style=custom_style,
    ).ask()

    if not proceed:
        console.print("[yellow]Cancelled[/yellow]")
        return

    # Create session
    session = session_manager.create_session(context)
    console.print(f"\n[dim]Session ID: {session.id}[/dim]")

    # Run pipeline
    run_full_pipeline(session, session_manager, config)


def continue_session(config, session_manager: SessionManager):
    """Continue a previous incomplete session."""
    clear_screen()
    show_banner()

    sessions = session_manager.get_incomplete_sessions()

    if not sessions:
        console.print("[yellow]No incomplete sessions found.[/yellow]")
        questionary.press_any_key_to_continue(style=custom_style).ask()
        return

    # Build choices
    choices = []
    for s in sessions[:10]:  # Limit to 10 most recent
        label = f"{s.id} - {s.user_context.title} [{s.current_step}]"
        choices.append(questionary.Choice(label, value=s.id))

    choices.append(questionary.Choice("Cancel", value=None))

    selected = questionary.select(
        "Select session to continue:",
        choices=choices,
        style=custom_style,
    ).ask()

    if not selected:
        return

    session = session_manager.load_session(selected)
    if not session:
        console.print("[red]Failed to load session[/red]")
        return

    show_context_summary(session.user_context)

    proceed = questionary.confirm(
        f"\nContinue from step: {session.current_step}?",
        default=True,
        style=custom_style,
    ).ask()

    if proceed:
        run_full_pipeline(session, session_manager, config)


def quick_pipeline(config):
    """Run pipeline without context (legacy mode)."""
    clear_screen()
    show_banner()

    console.print("[bold]Quick Pipeline Mode[/bold]")
    console.print("[dim]Running pipeline without custom context...[/dim]\n")

    proceed = questionary.confirm(
        "Run quick pipeline?",
        default=True,
        style=custom_style,
    ).ask()

    if not proceed:
        return

    # Run the original pipeline.py
    scripts_dir = Path(__file__).parent
    cmd = [sys.executable, str(scripts_dir / "pipeline.py"), "--skip-upload"]
    subprocess.run(cmd)


def show_sessions_history(session_manager: SessionManager):
    """Show history of all sessions."""
    clear_screen()
    show_banner()

    sessions = session_manager.list_sessions()

    if not sessions:
        console.print("[yellow]No sessions found.[/yellow]")
        questionary.press_any_key_to_continue(style=custom_style).ask()
        return

    table = Table(title="Session History")
    table.add_column("Session ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Status", style="green")
    table.add_column("Step", style="yellow")
    table.add_column("Created", style="dim")

    for s in sessions[:20]:
        status_color = {
            "completed": "green",
            "in_progress": "yellow",
            "failed": "red",
        }.get(s.status, "white")

        table.add_row(
            s.id,
            s.user_context.title[:30] + "..." if len(s.user_context.title) > 30 else s.user_context.title,
            f"[{status_color}]{s.status}[/{status_color}]",
            s.current_step,
            s.created_at[:10],
        )

    console.print(table)
    console.print()
    questionary.press_any_key_to_continue(style=custom_style).ask()


def generate_thumbnails_for_videos(videos: list[VideoFile]) -> dict:
    """
    Generate thumbnails for all videos with progress display.

    Returns:
        Dict mapping video path to list of thumbnail paths
    """
    thumbnails = {}

    # Check which videos need thumbnail generation
    videos_needing_thumbs = []
    for video in videos:
        thumb_paths = get_thumbnail_paths(video.path)
        if not all(p.exists() for p in thumb_paths):
            videos_needing_thumbs.append(video)
        else:
            thumbnails[str(video.path)] = thumb_paths

    if videos_needing_thumbs:
        console.print(f"\n[dim]Generating thumbnails for {len(videos_needing_thumbs)} videos...[/dim]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Thumbnails", total=len(videos_needing_thumbs))

            for video in videos_needing_thumbs:
                progress.update(task, description=f"[dim]{video.filename}[/dim]")
                thumb_paths = generate_thumbnails(video.path)
                thumbnails[str(video.path)] = thumb_paths
                progress.update(task, advance=1)

        console.print("[green]✓ Thumbnails ready[/green]\n")

    return thumbnails


def display_thumbnail_info(video: VideoFile, thumbnails: dict) -> bool:
    """
    Display thumbnails for a video inline if terminal supports it.

    Args:
        video: The VideoFile object
        thumbnails: Dict mapping video path to list of thumbnail paths

    Returns:
        True if inline thumbnails were displayed, False otherwise
    """
    thumb_paths = thumbnails.get(str(video.path), [])

    if not thumb_paths:
        console.print("  [dim]No thumbnails[/dim]")
        return False

    # Try to display inline thumbnails
    terminal = detect_terminal_image_support()

    if terminal != 'none':
        # Display thumbnails inline
        sys.stdout.write("  ")
        displayed = display_thumbnails_row(thumb_paths)
        if displayed:
            return True

    # Fallback: show text info
    console.print(f"  [dim]Thumbnails: {len(thumb_paths)} frames cached[/dim]")
    return False


def select_files_with_preview(videos: list[VideoFile], selector: FootageSelector) -> list[VideoFile]:
    """
    Interactive file selection with thumbnail support and preview capability.

    Controls:
    - Space: Toggle file selection
    - Enter: Confirm selection
    - p: Preview highlighted file in QuickTime
    """
    # Generate thumbnails first
    thumbnails = generate_thumbnails_for_videos(videos)

    console.print("[bold]File Selection[/bold]")
    console.print("[dim]Controls: [Space] Toggle | [p] Preview in QuickTime | [Enter] Confirm[/dim]\n")

    # Detect terminal support once
    terminal = detect_terminal_image_support()
    if terminal != 'none':
        console.print(f"[dim]Terminal supports inline images ({terminal})[/dim]\n")
    else:
        console.print("[dim]Terminal does not support inline images - showing text info[/dim]\n")

    # Display files with thumbnail info
    for i, video in enumerate(videos):
        console.print(f"{i+1}. {selector.format_file_choice(video)}")
        display_thumbnail_info(video, thumbnails)

    console.print()

    # Show thumbnail paths for reference
    console.print("[dim]Thumbnail cache location: ~/.cache/vlogging-workflow/thumbnails/[/dim]\n")

    # Offer preview before selection
    preview_choice = questionary.confirm(
        "Would you like to preview any videos before selecting?",
        default=False,
        style=custom_style,
    ).ask()

    if preview_choice:
        while True:
            preview_options = [
                questionary.Choice(f"{v.filename} ({v.duration_str})", value=v)
                for v in videos
            ]
            preview_options.append(questionary.Choice("Done previewing", value=None))

            to_preview = questionary.select(
                "Select a video to preview in QuickTime:",
                choices=preview_options,
                style=custom_style,
            ).ask()

            # Check if user selected "Done previewing" or cancelled
            # questionary may return None, the string label, or the VideoFile
            if not isinstance(to_preview, VideoFile):
                break

            console.print(f"[cyan]Opening {to_preview.filename} in QuickTime...[/cyan]")
            if preview_video(to_preview.path):
                console.print("[green]✓ QuickTime opened[/green]")
            else:
                console.print("[yellow]Could not open QuickTime[/yellow]")

            questionary.press_any_key_to_continue(
                message="Press any key to continue...",
                style=custom_style,
            ).ask()

    # Now do the actual file selection
    file_choices = []
    for video in videos:
        file_choices.append(questionary.Choice(
            selector.format_file_choice(video),
            value=video,
            checked=True,  # Default to selected
        ))

    selected_files = questionary.checkbox(
        "Select files to include (Space to toggle, Enter to confirm):",
        choices=file_choices,
        style=custom_style,
    ).ask()

    return selected_files


def select_thumbnail(video: VideoFile, thumbnails: dict) -> Path | None:
    """
    Interactive thumbnail selection for a video.

    Args:
        video: The VideoFile object
        thumbnails: Dict mapping video path to list of thumbnail paths

    Returns:
        Selected thumbnail path or None if cancelled
    """
    thumb_paths = thumbnails.get(str(video.path), [])

    if not thumb_paths:
        console.print("[yellow]No thumbnails available for this video.[/yellow]")
        return None

    # Display thumbnails inline if supported
    terminal = detect_terminal_image_support()
    if terminal != 'none':
        console.print("\n[bold]Thumbnail Preview:[/bold]")
        for i, thumb_path in enumerate(thumb_paths):
            position = ["10%", "50%", "90%"][i] if i < 3 else f"{i+1}"
            sys.stdout.write(f"  [{i+1}] {position}: ")
            display_inline_image(thumb_path, width=100)
            sys.stdout.write("\n")
        sys.stdout.flush()

    # Build choices
    choices = []
    for i, thumb_path in enumerate(thumb_paths):
        position = ["Beginning (10%)", "Middle (50%)", "End (90%)"][i] if i < 3 else f"Frame {i+1}"
        choices.append(questionary.Choice(position, value=thumb_path))

    choices.append(questionary.Choice("Skip thumbnail", value=None))

    selected = questionary.select(
        "Select thumbnail:",
        choices=choices,
        style=custom_style,
    ).ask()

    return selected


def collect_upload_metadata(
    video: VideoFile,
    config: dict,
    thumbnails: dict,
    index: int,
    total: int
) -> YouTubeUploadItem | None:
    """
    Collect upload metadata for a single video.

    Args:
        video: The VideoFile object
        config: Configuration dictionary
        thumbnails: Dict mapping video path to list of thumbnail paths
        index: Current video index (1-based)
        total: Total number of videos

    Returns:
        YouTubeUploadItem or None if cancelled
    """
    yt_config = config.get('youtube_direct', {})
    default_tags = yt_config.get('default_tags', ['vlog', 'video'])
    default_privacy = yt_config.get('default_privacy_status', 'private')
    default_category = yt_config.get('default_category_id', '22')
    description_footer = yt_config.get('description_footer', '')

    console.print(f"\n[bold cyan]Video {index}/{total}: {video.filename}[/bold cyan]")
    console.print(f"[dim]Duration: {video.duration_str} | Resolution: {video.resolution_str or 'Unknown'}[/dim]\n")

    # Display thumbnails
    thumb_paths = thumbnails.get(str(video.path), [])
    if thumb_paths:
        terminal = detect_terminal_image_support()
        if terminal != 'none':
            display_thumbnails_row(thumb_paths)

    # Select thumbnail
    thumbnail_path = select_thumbnail(video, thumbnails)

    # Title (default to filename without extension)
    default_title = video.path.stem.replace('_', ' ').replace('-', ' ')
    title = questionary.text(
        "Video title:",
        default=default_title,
        style=custom_style,
    ).ask()

    if title is None:
        return None

    # Description
    description = questionary.text(
        "Video description:",
        instruction="(Leave empty for no description)",
        style=custom_style,
    ).ask()

    if description is None:
        return None

    # Append footer if configured
    if description_footer:
        description = f"{description}\n\n{description_footer}" if description else description_footer

    # Tags
    console.print(f"\n[dim]Default tags: {', '.join(default_tags)}[/dim]")
    custom_tags = questionary.text(
        "Additional tags:",
        instruction="(Comma-separated, leave empty for defaults only)",
        style=custom_style,
    ).ask()

    if custom_tags is None:
        return None

    tags = default_tags.copy()
    if custom_tags.strip():
        tags.extend([t.strip() for t in custom_tags.split(',') if t.strip()])

    # Privacy status
    privacy = questionary.select(
        "Privacy status:",
        choices=[
            questionary.Choice("Private (only you can see)", value="private"),
            questionary.Choice("Unlisted (anyone with link)", value="unlisted"),
            questionary.Choice("Public (everyone can see)", value="public"),
        ],
        default=default_privacy,
        style=custom_style,
    ).ask()

    if privacy is None:
        return None

    return YouTubeUploadItem(
        video_path=str(video.path),
        thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        title=title,
        description=description or "",
        tags=tags,
        privacy_status=privacy,
        category_id=default_category,
    )


def review_upload_queue(items: list[YouTubeUploadItem]) -> list[YouTubeUploadItem]:
    """
    Display upload queue for review and allow editing.

    Args:
        items: List of YouTubeUploadItem objects

    Returns:
        Updated list (possibly with removed items) or empty list if cancelled
    """
    while True:
        clear_screen()
        show_banner()

        console.print("[bold]Upload Queue Review[/bold]\n")

        # Build table
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=3)
        table.add_column("File", style="white", width=25)
        table.add_column("Title", style="green", width=30)
        table.add_column("Tags", style="dim", width=20)
        table.add_column("Privacy", style="yellow", width=10)
        table.add_column("Thumbnail", style="dim", width=10)

        for i, item in enumerate(items, 1):
            filename = Path(item.video_path).name
            if len(filename) > 24:
                filename = filename[:21] + "..."
            title = item.title[:29] + "..." if len(item.title) > 29 else item.title
            tags_str = ", ".join(item.tags[:3])
            if len(item.tags) > 3:
                tags_str += f" (+{len(item.tags)-3})"
            thumb_status = "Yes" if item.thumbnail_path else "No"

            table.add_row(
                str(i),
                filename,
                title,
                tags_str,
                item.privacy_status,
                thumb_status,
            )

        console.print(table)
        console.print()

        # Options
        action = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice(f"Upload all {len(items)} videos", value="upload"),
                questionary.Choice("Remove a video from queue", value="remove"),
                questionary.Choice("Cancel upload", value="cancel"),
            ],
            style=custom_style,
        ).ask()

        if action is None or action == "cancel":
            return []
        elif action == "upload":
            return items
        elif action == "remove":
            # Select which to remove
            remove_choices = [
                questionary.Choice(f"{i}. {Path(item.video_path).name}", value=i-1)
                for i, item in enumerate(items, 1)
            ]
            remove_choices.append(questionary.Choice("Cancel", value=-1))

            to_remove = questionary.select(
                "Select video to remove:",
                choices=remove_choices,
                style=custom_style,
            ).ask()

            if to_remove is not None and to_remove >= 0:
                removed = items.pop(to_remove)
                console.print(f"[yellow]Removed: {Path(removed.video_path).name}[/yellow]")

            if not items:
                console.print("[yellow]No videos left in queue.[/yellow]")
                questionary.press_any_key_to_continue(style=custom_style).ask()
                return []


def save_upload_history(
    results: list[YouTubeUploadResult],
    items: list[YouTubeUploadItem],
    config: dict
) -> Path:
    """
    Save upload results to logs/upload_history.json.

    Args:
        results: List of YouTubeUploadResult objects
        items: List of YouTubeUploadItem objects (for metadata)
        config: Configuration dictionary

    Returns:
        Path to the history file
    """
    import json

    base_dir = Path(config['project']['base_dir'])
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    history_file = logs_dir / "upload_history.json"

    # Load existing history
    history = []
    if history_file.exists():
        try:
            with open(history_file, 'r') as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []

    # Create item lookup by video_path
    items_by_path = {item.video_path: item for item in items}

    # Add new entries
    timestamp = datetime.now().isoformat()
    for result in results:
        item = items_by_path.get(result.video_path)
        entry = {
            "timestamp": timestamp,
            "filename": Path(result.video_path).name,
            "video_path": result.video_path,
            "success": result.success,
            "video_id": result.video_id,
            "video_url": result.video_url,
            "title": item.title if item else None,
            "privacy_status": item.privacy_status if item else None,
            "tags": item.tags if item else [],
            "error": result.error,
        }
        history.append(entry)

    # Save updated history
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=2)

    return history_file


def execute_upload_queue(items: list[YouTubeUploadItem], config: dict) -> list[YouTubeUploadResult]:
    """
    Execute uploads for all items in the queue.

    Saves progress after each upload for resume capability.

    Args:
        items: List of YouTubeUploadItem objects
        config: Configuration dictionary

    Returns:
        List of YouTubeUploadResult objects
    """
    # Import upload functions
    from upload import get_authenticated_service, upload_video_direct

    results = []
    remaining_items = items.copy()
    yt_config = config.get('youtube_direct', {})
    made_for_kids = yt_config.get('made_for_kids', False)

    # Save initial queue for resume capability
    save_upload_queue(remaining_items, config)
    console.print(f"[dim]Queue saved for resume capability ({len(items)} videos)[/dim]")

    console.print("\n[bold]Authenticating with YouTube...[/bold]")
    youtube = get_authenticated_service(config)

    if youtube is None:
        console.print("[red]Failed to authenticate with YouTube.[/red]")
        return [YouTubeUploadResult(
            video_path=item.video_path,
            success=False,
            error="Authentication failed"
        ) for item in items]

    console.print("[green]Authenticated successfully[/green]\n")

    # Upload each video
    for i, item in enumerate(items, 1):
        console.print(f"\n[bold]Uploading {i}/{len(items)}[/bold]")

        upload_result = upload_video_direct(
            youtube=youtube,
            video_path=item.video_path,
            title=item.title,
            description=item.description,
            tags=item.tags,
            category_id=item.category_id,
            privacy_status=item.privacy_status,
            thumbnail_path=item.thumbnail_path,
            made_for_kids=made_for_kids,
        )

        result = YouTubeUploadResult(
            video_path=item.video_path,
            success=upload_result['success'],
            video_id=upload_result.get('video_id'),
            video_url=upload_result.get('video_url'),
            error=upload_result.get('error'),
        )
        results.append(result)

        # Remove successful upload from remaining queue
        if result.success:
            remaining_items = [r for r in remaining_items if r.video_path != item.video_path]
            update_upload_queue(remaining_items, config)

    # Clear queue if all successful
    if all(r.success for r in results):
        clear_upload_queue(config)

    return results


def _show_upload_results(results: list[YouTubeUploadResult], history_file: Path) -> None:
    """Display upload results in a panel."""
    console.print("\n")
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count

    console.print(Panel.fit(
        f"[bold]Upload Complete[/bold]\n\n"
        f"Successful: [green]{success_count}[/green]\n"
        f"Failed: [red]{fail_count}[/red]\n\n"
        + "\n".join([
            f"[green]✓[/green] {Path(r.video_path).name}: {r.video_url}"
            if r.success else
            f"[red]✗[/red] {Path(r.video_path).name}: {r.error}"
            for r in results
        ])
        + f"\n\n[dim]History saved to: {history_file}[/dim]",
        title="Results",
        border_style="green" if fail_count == 0 else "yellow",
    ))

    questionary.press_any_key_to_continue(style=custom_style).ask()


def youtube_upload_workflow(config):
    """Main YouTube upload workflow - upload videos directly from SD card."""
    clear_screen()
    show_banner()

    console.print("[bold cyan]YouTube Direct Upload[/bold cyan]")
    console.print("[dim]Upload videos directly from SD card to YouTube[/dim]\n")

    # Check for pending uploads to resume
    pending_items = load_upload_queue(config)
    if pending_items:
        console.print(f"[yellow]Found {len(pending_items)} pending uploads from previous session[/yellow]\n")

        # Show what's pending
        for i, item in enumerate(pending_items, 1):
            console.print(f"  {i}. {Path(item.video_path).name} - {item.title}")

        console.print()

        resume_choice = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice(f"Resume uploading {len(pending_items)} videos", value="resume"),
                questionary.Choice("Discard and start fresh", value="discard"),
                questionary.Choice("Cancel", value="cancel"),
            ],
            style=custom_style,
        ).ask()

        if resume_choice == "cancel":
            return
        elif resume_choice == "resume":
            # Skip to upload step with pending items
            console.print("\n[bold]Resuming uploads...[/bold]")
            results = execute_upload_queue(pending_items, config)

            # Save history and show results
            history_file = save_upload_history(results, pending_items, config)
            _show_upload_results(results, history_file)
            return
        else:
            # Discard pending queue
            clear_upload_queue(config)
            console.print("[dim]Previous queue discarded[/dim]\n")

    # Step 1: Scan SD card
    console.print("[bold]Step 1: Select Videos[/bold]\n")

    sd_path, day_groups = scan_sd_card(config)

    if sd_path is None:
        console.print("[red]SD card not found![/red]")
        console.print(f"[dim]Looking for volume: {config.get('sd_card', {}).get('volume_name', 'Untitled')}[/dim]")
        console.print("[dim]Make sure your SD card is mounted.[/dim]\n")
        questionary.press_any_key_to_continue(style=custom_style).ask()
        return

    if not day_groups:
        console.print("[yellow]No video files found on SD card.[/yellow]")
        questionary.press_any_key_to_continue(style=custom_style).ask()
        return

    selector = FootageSelector(config)

    # Show summary
    total_files = sum(g.file_count for g in day_groups)
    total_duration = sum(g.total_duration for g in day_groups)
    console.print(f"[green]Found {total_files} videos across {len(day_groups)} days[/green]")
    console.print(f"[dim]Total duration: {int(total_duration // 3600)}h {int((total_duration % 3600) // 60)}m[/dim]\n")

    # Select day
    day_choices = []
    for group in day_groups:
        day_choices.append(questionary.Choice(
            selector.format_day_choice(group),
            value=group.date
        ))
    day_choices.append(questionary.Choice("Cancel", value=None))

    selected_date = questionary.select(
        "Select footage date:",
        choices=day_choices,
        style=custom_style,
    ).ask()

    if selected_date is None:
        return

    # Find the selected day group
    selected_group = next(g for g in day_groups if g.date == selected_date)

    # Select files with preview
    selected_files = select_files_with_preview(selected_group.files, selector)

    if not selected_files:
        console.print("[yellow]No files selected.[/yellow]")
        return

    console.print(f"\n[green]Selected {len(selected_files)} videos[/green]\n")

    # Step 2: Generate thumbnails
    console.print("[bold]Step 2: Generate Thumbnails[/bold]\n")
    thumbnails = generate_thumbnails_for_videos(selected_files)

    # Step 3: Collect metadata for each video
    console.print("[bold]Step 3: Enter Video Details[/bold]")

    upload_items = []
    for i, video in enumerate(selected_files, 1):
        item = collect_upload_metadata(video, config, thumbnails, i, len(selected_files))
        if item is None:
            console.print("[yellow]Cancelled[/yellow]")
            return
        upload_items.append(item)

    # Step 4: Review queue
    console.print("\n[bold]Step 4: Review Upload Queue[/bold]")
    upload_items = review_upload_queue(upload_items)

    if not upload_items:
        console.print("[yellow]Upload cancelled[/yellow]")
        return

    # Step 5: Confirm and upload
    proceed = questionary.confirm(
        f"\nReady to upload {len(upload_items)} videos to YouTube?",
        default=True,
        style=custom_style,
    ).ask()

    if not proceed:
        console.print("[yellow]Upload cancelled[/yellow]")
        return

    console.print("\n[bold]Step 5: Uploading to YouTube[/bold]")
    results = execute_upload_queue(upload_items, config)

    # Save upload history and show results
    history_file = save_upload_history(results, upload_items, config)
    _show_upload_results(results, history_file)


def show_settings(config):
    """Show current settings."""
    clear_screen()
    show_banner()

    console.print(Panel.fit(
        f"[bold]Project Settings[/bold]\n"
        f"  Base directory: {config['project']['base_dir']}\n"
        f"  Target duration: {config['project']['target_duration_minutes']} minutes\n\n"
        f"[bold]Output Settings[/bold]\n"
        f"  Resolution: {config['output']['resolution']}\n"
        f"  FPS: {config['output']['fps']}\n"
        f"  Codec: {config['output']['codec']}\n\n"
        f"[bold]AI Settings[/bold]\n"
        f"  Provider: {config['ai']['provider']}\n"
        f"  Model: {config['ai']['model']}\n\n"
        f"[bold]Music Settings[/bold]\n"
        f"  Library: {config.get('music', {}).get('library_path', 'music')}\n"
        f"  Default volume: {config.get('music', {}).get('default_volume', 0.15)}\n\n"
        f"[bold]Subtitle Settings[/bold]\n"
        f"  Font size: {config.get('subtitles', {}).get('font_size', 24)}\n"
        f"  Position: {config.get('subtitles', {}).get('position', 'bottom')}",
        title="Settings",
        border_style="cyan",
    ))

    console.print("\n[dim]Edit config.yaml to change settings[/dim]\n")
    questionary.press_any_key_to_continue(style=custom_style).ask()


def main_menu():
    """Main application menu."""
    config = load_config()
    base_dir = Path(config['project']['base_dir'])
    session_manager = SessionManager(base_dir)

    while True:
        clear_screen()
        show_banner()

        # Check for incomplete sessions
        incomplete = session_manager.get_incomplete_sessions()
        if incomplete:
            console.print(f"[yellow]You have {len(incomplete)} incomplete session(s)[/yellow]\n")

        choice = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("Create New Video", value="new"),
                questionary.Choice("Upload to YouTube", value="youtube_upload"),
                questionary.Choice("Continue Previous Session", value="continue"),
                questionary.Choice("Quick Pipeline (no context)", value="quick"),
                questionary.Choice("View Session History", value="history"),
                questionary.Choice("Settings", value="settings"),
                questionary.Choice("Exit", value="exit"),
            ],
            style=custom_style,
        ).ask()

        if choice is None or choice == "exit":
            console.print("\n[cyan]Goodbye![/cyan]\n")
            break
        elif choice == "new":
            create_new_video(config, session_manager)
        elif choice == "youtube_upload":
            youtube_upload_workflow(config)
        elif choice == "continue":
            continue_session(config, session_manager)
        elif choice == "quick":
            quick_pipeline(config)
        elif choice == "history":
            show_sessions_history(session_manager)
        elif choice == "settings":
            show_settings(config)


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(0)
