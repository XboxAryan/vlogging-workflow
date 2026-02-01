#!/usr/bin/env python3
"""
Cleanup Script
==============
Archives or deletes raw footage after successful upload.
"""

import os
import sys
import json
import shutil
import zipfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_folder_size(path):
    """Calculate total size of a folder"""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total

def format_size(bytes_size):
    """Format file size in human-readable form"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"

def archive_project(project_path, archive_dir):
    """Archive a project's raw footage"""
    project_path = Path(project_path)
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)

    raw_dir = project_path / "raw"
    if not raw_dir.exists():
        return None

    # Load manifest for date
    manifest_path = project_path / "manifest.json"
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    project_date = manifest.get('date', project_path.name)
    archive_name = f"raw_{project_date}.zip"
    archive_path = archive_dir / archive_name

    console.print(f"  Archiving {raw_dir.name}...")

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in raw_dir.iterdir():
            if file.is_file():
                zipf.write(file, file.name)
                console.print(f"    + {file.name}")

    return archive_path

def delete_raw_footage(project_path):
    """Delete raw footage from a project"""
    project_path = Path(project_path)
    raw_dir = project_path / "raw"
    audio_dir = project_path / "audio"

    deleted_size = 0

    for dir_path in [raw_dir, audio_dir]:
        if dir_path.exists():
            deleted_size += get_folder_size(dir_path)
            shutil.rmtree(dir_path)
            dir_path.mkdir()  # Recreate empty directory
            console.print(f"  [green]✓[/green] Deleted {dir_path.name}/")

    return deleted_size

def cleanup_old_archives(archive_dir, days_old, dry_run=False):
    """Delete archives older than specified days"""
    archive_dir = Path(archive_dir)
    if not archive_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=days_old)
    deleted_size = 0
    deleted_count = 0

    for archive in archive_dir.glob("*.zip"):
        mtime = datetime.fromtimestamp(archive.stat().st_mtime)
        if mtime < cutoff:
            size = archive.stat().st_size
            if dry_run:
                console.print(f"  Would delete: {archive.name} ({format_size(size)})")
            else:
                archive.unlink()
                console.print(f"  [green]✓[/green] Deleted: {archive.name}")
            deleted_size += size
            deleted_count += 1

    return deleted_size, deleted_count

def process_projects(config, dry_run=False, force=False):
    """Process all projects that have been uploaded"""
    base_dir = Path(config['project']['base_dir'])
    projects_dir = base_dir / "projects"
    archive_dir = base_dir / "archive"

    if not projects_dir.exists():
        console.print("[yellow]No projects found[/yellow]")
        return

    cleanup_action = config['cleanup']['action']

    # Find projects ready for cleanup (uploaded successfully)
    ready_projects = []

    for project_path in sorted(projects_dir.iterdir()):
        if not project_path.is_dir():
            continue

        manifest_path = project_path / "manifest.json"
        if not manifest_path.exists():
            continue

        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        # Check if uploaded
        if 'upload' in manifest and manifest['upload'].get('video_id'):
            raw_dir = project_path / "raw"
            if raw_dir.exists() and any(raw_dir.iterdir()):
                raw_size = get_folder_size(raw_dir)
                ready_projects.append({
                    'path': project_path,
                    'date': manifest.get('date', 'unknown'),
                    'video_id': manifest['upload']['video_id'],
                    'raw_size': raw_size
                })

    if not ready_projects:
        console.print("[green]No projects ready for cleanup (all clean or not uploaded)[/green]")
        return

    # Display summary
    table = Table(title="Projects Ready for Cleanup")
    table.add_column("Date", style="cyan")
    table.add_column("YouTube ID", style="green")
    table.add_column("Raw Size", justify="right")

    total_size = 0
    for proj in ready_projects:
        table.add_row(
            proj['date'],
            proj['video_id'],
            format_size(proj['raw_size'])
        )
        total_size += proj['raw_size']

    table.add_row(
        "[bold]Total[/bold]",
        "",
        f"[bold]{format_size(total_size)}[/bold]"
    )

    console.print(table)
    console.print(f"\nCleanup action: [bold]{cleanup_action}[/bold]")

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made[/yellow]")
        return

    if not force:
        if not Confirm.ask(f"\nProceed with cleanup ({cleanup_action})?"):
            console.print("[yellow]Cleanup cancelled[/yellow]")
            return

    # Perform cleanup
    console.print(f"\n[bold]Performing cleanup...[/bold]")

    for proj in ready_projects:
        console.print(f"\n[cyan]Project: {proj['date']}[/cyan]")

        if cleanup_action == 'archive':
            archive_path = archive_project(proj['path'], archive_dir)
            if archive_path:
                console.print(f"  [green]✓[/green] Archived to {archive_path.name}")
                delete_raw_footage(proj['path'])
        elif cleanup_action == 'delete':
            deleted = delete_raw_footage(proj['path'])
            console.print(f"  [green]✓[/green] Freed {format_size(deleted)}")
        # 'keep' does nothing

    # Clean old archives if configured
    delete_days = config['cleanup']['delete_archives_after_days']
    if delete_days > 0 and cleanup_action == 'archive':
        console.print(f"\n[bold]Checking for archives older than {delete_days} days...[/bold]")
        deleted_size, deleted_count = cleanup_old_archives(archive_dir, delete_days)
        if deleted_count > 0:
            console.print(f"  Deleted {deleted_count} old archive(s), freed {format_size(deleted_size)}")

    console.print(Panel.fit(
        f"[bold green]Cleanup complete![/bold green]\n"
        f"Freed approximately {format_size(total_size)}",
        title="Done"
    ))

def main():
    """Main entry point"""
    config = load_config()

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if dry_run:
        console.print("[yellow]Running in dry-run mode[/yellow]\n")

    process_projects(config, dry_run=dry_run, force=force)

if __name__ == "__main__":
    main()
