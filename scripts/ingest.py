#!/usr/bin/env python3
"""
Footage Ingestion Script
========================
Copies footage from SD card and organizes it by date.
"""

import os
import sys
import shutil
import hashlib
import re
from pathlib import Path
from datetime import datetime
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

from concurrent_utils import get_worker_count, is_concurrency_enabled, parallel_map


def slugify(text):
    """
    Convert text to a URL/filesystem-friendly slug.
    Example: "Morning Coffee Routine!" -> "morning-coffee-routine"
    """
    if not text:
        return ""
    # Convert to lowercase
    text = text.lower()
    # Replace spaces and underscores with hyphens
    text = re.sub(r'[\s_]+', '-', text)
    # Remove any characters that aren't alphanumeric or hyphens
    text = re.sub(r'[^a-z0-9-]', '', text)
    # Remove multiple consecutive hyphens
    text = re.sub(r'-+', '-', text)
    # Strip leading/trailing hyphens
    text = text.strip('-')
    return text

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.table import Table

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_video_metadata(filepath):
    """Extract metadata from video file using ffprobe"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            str(filepath)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not read metadata from {filepath}: {e}[/yellow]")
    return None

def get_video_duration(metadata):
    """Extract duration from video metadata"""
    if metadata and 'format' in metadata:
        duration = metadata['format'].get('duration')
        if duration:
            return float(duration)
    return 0

def get_video_creation_date(filepath, metadata):
    """Get creation date from video metadata or file stats"""
    # Try to get from metadata first
    if metadata and 'format' in metadata:
        tags = metadata['format'].get('tags', {})
        creation_time = tags.get('creation_time')
        if creation_time:
            try:
                # Parse ISO format datetime
                dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
                return dt.strftime('%Y-%m-%d')
            except:
                pass

    # Fall back to file modification time
    mtime = os.path.getmtime(filepath)
    return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

def calculate_file_hash(filepath, chunk_size=8192):
    """Calculate MD5 hash of a file for verification"""
    hash_md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def copy_with_progress(src, dst, description="Copying"):
    """Copy file with progress indication"""
    src = Path(src)
    dst = Path(dst)
    file_size = src.stat().st_size

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task(description, total=file_size)

        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                chunk = fsrc.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                fdst.write(chunk)
                progress.update(task, advance=len(chunk))

def format_duration(seconds):
    """Format duration in human-readable form"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def format_size(bytes_size):
    """Format file size in human-readable form"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"

def ingest_footage(config, dry_run=False, date_filter=None, title=None):
    """
    Main ingestion function.

    Args:
        config: Configuration dictionary
        dry_run: If True, only show what would be done
        date_filter: If set, only ingest files from this date (YYYY-MM-DD or YYYYMMDD)
        title: Optional title for project directory naming (creates {slug}_{date} format)
    """
    base_dir = Path(config['project']['base_dir'])
    volume_name = config['sd_card']['volume_name']
    footage_subpath = config['sd_card']['footage_path']
    extensions = config['sd_card']['extensions']

    sd_footage_path = Path(f"/Volumes/{volume_name}") / footage_subpath

    # Check if SD card is mounted
    if not sd_footage_path.exists():
        console.print(f"[red]Error:[/red] SD card footage path not found: {sd_footage_path}")
        console.print(f"Make sure your SD card '{volume_name}' is mounted.")
        return None

    # Find all video files
    video_files = []
    for ext in extensions:
        video_files.extend(sd_footage_path.glob(f"*{ext}"))

    if not video_files:
        console.print("[yellow]No video files found on SD card[/yellow]")
        return None

    console.print(Panel.fit(
        f"[bold]Found {len(video_files)} video file(s)[/bold]",
        title="Ingestion"
    ))

    # Analyze and group by date - parallel metadata extraction
    files_by_date = {}
    total_size = 0
    total_duration = 0

    def analyze_video_file(vf):
        """Extract metadata from a single video file."""
        metadata = get_video_metadata(vf)
        date = get_video_creation_date(vf, metadata)
        duration = get_video_duration(metadata)
        size = vf.stat().st_size
        return {
            'path': vf,
            'name': vf.name,
            'size': size,
            'duration': duration,
            'metadata': metadata,
            'date': date
        }

    # Parallel metadata extraction
    max_workers = get_worker_count(config, 'ffprobe')
    use_parallel = is_concurrency_enabled(config) and len(video_files) > 1

    if use_parallel and max_workers > 1:
        console.print(f"[dim]Analyzing {len(video_files)} files ({max_workers} workers)...[/dim]")
        results = parallel_map(
            analyze_video_file,
            video_files,
            max_workers=max_workers,
            description="Analyzing videos",
            console=console
        )
        analyzed_files = [r.result for r in results if r.success and r.result is not None]
    else:
        console.print(f"[dim]Analyzing {len(video_files)} files...[/dim]")
        analyzed_files = [analyze_video_file(vf) for vf in video_files]

    # Group by date
    for file_info in analyzed_files:
        date = file_info['date']
        total_size += file_info['size']
        total_duration += file_info['duration']

        if date not in files_by_date:
            files_by_date[date] = []

        files_by_date[date].append({
            'path': file_info['path'],
            'name': file_info['name'],
            'size': file_info['size'],
            'duration': file_info['duration'],
            'metadata': file_info['metadata']
        })

    # Apply date filter if specified
    if date_filter:
        # Normalize date filter format
        date_filter_normalized = date_filter.replace('-', '')
        filtered = {}
        for date, files in files_by_date.items():
            date_normalized = date.replace('-', '')
            if date_normalized == date_filter_normalized:
                filtered[date] = files

        if not filtered:
            console.print(f"[yellow]No files found for date: {date_filter}[/yellow]")
            console.print(f"Available dates: {', '.join(sorted(files_by_date.keys()))}")
            return None

        files_by_date = filtered
        video_files = [f['path'] for files in filtered.values() for f in files]
        total_size = sum(f['size'] for files in filtered.values() for f in files)
        total_duration = sum(f['duration'] for files in filtered.values() for f in files)

        console.print(f"[cyan]Filtered to date: {date_filter}[/cyan]\n")

    # Display summary
    table = Table(title="Footage Summary")
    table.add_column("Date", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")

    for date in sorted(files_by_date.keys()):
        files = files_by_date[date]
        date_duration = sum(f['duration'] for f in files)
        date_size = sum(f['size'] for f in files)
        table.add_row(
            date,
            str(len(files)),
            format_duration(date_duration),
            format_size(date_size)
        )

    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{len(video_files)}[/bold]",
        f"[bold]{format_duration(total_duration)}[/bold]",
        f"[bold]{format_size(total_size)}[/bold]"
    )

    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run - no files copied[/yellow]")
        return files_by_date

    # Copy files
    console.print("\n[bold]Copying files...[/bold]")

    copied_projects = []

    for date in sorted(files_by_date.keys()):
        files = files_by_date[date]
        # Create project directory name: {slug}_{date} or just {date}
        if title:
            slug = slugify(title)
            project_name = f"{slug}_{date}" if slug else date
        else:
            project_name = date
        project_dir = base_dir / "projects" / project_name

        # Create project directory structure
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "raw").mkdir(exist_ok=True)
        (project_dir / "audio").mkdir(exist_ok=True)
        (project_dir / "transcripts").mkdir(exist_ok=True)
        (project_dir / "output").mkdir(exist_ok=True)

        console.print(f"\n[cyan]Project: {date}[/cyan]")

        manifest = {
            'date': date,
            'ingested_at': datetime.now().isoformat(),
            'files': []
        }

        for file_info in files:
            src = file_info['path']
            dst = project_dir / "raw" / file_info['name']

            console.print(f"  {file_info['name']} ({format_size(file_info['size'])})")

            if not dst.exists():
                copy_with_progress(src, dst, description=f"    Copying {file_info['name']}")

                # Verify copy - calculate hashes in parallel
                with ThreadPoolExecutor(max_workers=2) as executor:
                    src_future = executor.submit(calculate_file_hash, src)
                    dst_future = executor.submit(calculate_file_hash, dst)
                    src_hash = src_future.result()
                    dst_hash = dst_future.result()

                if src_hash != dst_hash:
                    console.print(f"    [red]Hash mismatch! Copy may be corrupted.[/red]")
                    dst.unlink()
                    continue
                else:
                    console.print(f"    [green]✓ Verified[/green]")
            else:
                console.print(f"    [yellow]Already exists, skipping[/yellow]")

            manifest['files'].append({
                'name': file_info['name'],
                'duration': file_info['duration'],
                'size': file_info['size'],
                'path': str(dst)
            })

        # Save manifest
        manifest_path = project_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        copied_projects.append({
            'date': date,
            'path': project_dir,
            'files': manifest['files']
        })

    console.print(Panel.fit(
        f"[bold green]Ingestion complete![/bold green]\n"
        f"Created {len(copied_projects)} project(s)",
        title="Done"
    ))

    return copied_projects


def ingest_specific_files(config, file_list_path, dry_run=False):
    """
    Ingest specific files from a JSON file list.

    Args:
        config: Configuration dictionary
        file_list_path: Path to JSON file containing {"files": [...], "date": "YYYY-MM-DD", "title": "..."}
        dry_run: If True, only show what would be done

    Returns:
        List of project info dicts
    """
    base_dir = Path(config['project']['base_dir'])

    # Load file list
    with open(file_list_path, 'r') as f:
        file_data = json.load(f)

    file_paths = [Path(p) for p in file_data.get('files', [])]
    project_date = file_data.get('date', datetime.now().strftime('%Y-%m-%d'))
    title = file_data.get('title', '')

    # Filter to existing files only
    video_files = [p for p in file_paths if p.exists()]

    if not video_files:
        console.print("[red]Error: None of the specified files exist[/red]")
        return None

    # Build display info
    display_info = f"[bold]Ingesting {len(video_files)} selected file(s)[/bold]\n" \
                   f"Date: {project_date}"
    if title:
        display_info += f"\nTitle: {title}"

    console.print(Panel.fit(display_info, title="Selective Ingestion"))

    # Analyze files - parallel metadata extraction
    def analyze_video_file(vf):
        """Extract metadata from a single video file."""
        metadata = get_video_metadata(vf)
        duration = get_video_duration(metadata)
        size = vf.stat().st_size
        return {
            'path': vf,
            'name': vf.name,
            'size': size,
            'duration': duration,
            'metadata': metadata
        }

    max_workers = get_worker_count(config, 'ffprobe')
    use_parallel = is_concurrency_enabled(config) and len(video_files) > 1

    if use_parallel and max_workers > 1:
        console.print(f"[dim]Analyzing {len(video_files)} files ({max_workers} workers)...[/dim]")
        results = parallel_map(
            analyze_video_file,
            video_files,
            max_workers=max_workers,
            description="Analyzing videos",
            console=console
        )
        files_info = [r.result for r in results if r.success and r.result is not None]
    else:
        console.print(f"[dim]Analyzing {len(video_files)} files...[/dim]")
        files_info = [analyze_video_file(vf) for vf in video_files]

    total_size = sum(f['size'] for f in files_info)
    total_duration = sum(f['duration'] for f in files_info)

    # Display summary
    table = Table(title="Selected Files")
    table.add_column("File", style="cyan")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")

    for f in files_info:
        table.add_row(
            f['name'][:40],
            format_duration(f['duration']),
            format_size(f['size'])
        )

    table.add_row(
        f"[bold]Total ({len(files_info)} files)[/bold]",
        f"[bold]{format_duration(total_duration)}[/bold]",
        f"[bold]{format_size(total_size)}[/bold]"
    )

    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run - no files copied[/yellow]")
        return [{'date': project_date, 'files': files_info}]

    # Create project directory name: {slug}_{date} or just {date}
    if title:
        slug = slugify(title)
        project_name = f"{slug}_{project_date}" if slug else project_date
    else:
        project_name = project_date
    project_dir = base_dir / "projects" / project_name

    # Create project directory structure
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "raw").mkdir(exist_ok=True)
    (project_dir / "audio").mkdir(exist_ok=True)
    (project_dir / "transcripts").mkdir(exist_ok=True)
    (project_dir / "output").mkdir(exist_ok=True)

    console.print(f"\n[cyan]Project: {project_date}[/cyan]")
    console.print("\n[bold]Copying files...[/bold]")

    manifest = {
        'date': project_date,
        'title': title if title else None,
        'ingested_at': datetime.now().isoformat(),
        'files': []
    }

    for file_info in files_info:
        src = file_info['path']
        dst = project_dir / "raw" / file_info['name']

        console.print(f"  {file_info['name']} ({format_size(file_info['size'])})")

        if not dst.exists():
            copy_with_progress(src, dst, description=f"    Copying {file_info['name']}")

            # Verify copy - calculate hashes in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                src_future = executor.submit(calculate_file_hash, src)
                dst_future = executor.submit(calculate_file_hash, dst)
                src_hash = src_future.result()
                dst_hash = dst_future.result()

            if src_hash != dst_hash:
                console.print(f"    [red]Hash mismatch! Copy may be corrupted.[/red]")
                dst.unlink()
                continue
            else:
                console.print(f"    [green]✓ Verified[/green]")
        else:
            console.print(f"    [yellow]Already exists, skipping[/yellow]")

        manifest['files'].append({
            'name': file_info['name'],
            'duration': file_info['duration'],
            'size': file_info['size'],
            'path': str(dst)
        })

    # Save manifest
    manifest_path = project_dir / "manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    console.print(Panel.fit(
        f"[bold green]Ingestion complete![/bold green]\n"
        f"Project: {project_dir}",
        title="Done"
    ))

    return [{
        'date': project_date,
        'path': project_dir,
        'files': manifest['files']
    }]


def main():
    """Main entry point"""
    config = load_config()

    dry_run = "--dry-run" in sys.argv

    # Parse --file-list argument (for selective ingestion)
    file_list_path = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--file-list' and i < len(sys.argv) - 1:
            file_list_path = sys.argv[i + 1]
        elif arg.startswith('--file-list='):
            file_list_path = arg.split('=', 1)[1]

    # Parse --date argument
    date_filter = None
    for arg in sys.argv[1:]:
        if arg.startswith('--date='):
            date_filter = arg.split('=')[1]
        elif arg == '--date' and sys.argv.index(arg) + 1 < len(sys.argv):
            date_filter = sys.argv[sys.argv.index(arg) + 1]

    if dry_run:
        console.print("[yellow]Running in dry-run mode[/yellow]\n")

    # Use selective ingestion if file list provided
    if file_list_path:
        result = ingest_specific_files(config, file_list_path, dry_run=dry_run)
    else:
        result = ingest_footage(config, dry_run=dry_run, date_filter=date_filter)

    if result:
        # Output the most recent project path for pipeline use
        if not dry_run:
            latest = sorted(result, key=lambda x: x['date'])[-1]
            print(f"\nLATEST_PROJECT={latest['path']}")

if __name__ == "__main__":
    main()
