#!/usr/bin/env python3
"""
SD Card Watcher
===============
Monitors for SD card insertion and triggers the vlogging workflow.
Can be run manually or as a launchd daemon.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from rich.console import Console
from rich.panel import Panel

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def check_sd_card(config):
    """Check if the configured SD card is mounted"""
    volume_name = config['sd_card']['volume_name']
    volume_path = Path(f"/Volumes/{volume_name}")
    return volume_path.exists()

def get_footage_path(config):
    """Get the full path to footage on the SD card"""
    volume_name = config['sd_card']['volume_name']
    footage_subpath = config['sd_card']['footage_path']
    return Path(f"/Volumes/{volume_name}") / footage_subpath

def has_new_footage(config):
    """Check if there are video files on the SD card"""
    footage_path = get_footage_path(config)
    if not footage_path.exists():
        return False

    extensions = config['sd_card']['extensions']
    for ext in extensions:
        if list(footage_path.glob(f"*{ext}")):
            return True
    return False

def trigger_workflow():
    """Run the main workflow pipeline"""
    scripts_dir = Path(__file__).parent

    console.print(Panel.fit(
        "[bold green]SD Card Detected![/bold green]\n"
        "Starting vlogging workflow...",
        title="Vlog Workflow"
    ))

    # Run the pipeline script
    pipeline_script = scripts_dir / "pipeline.py"
    if pipeline_script.exists():
        subprocess.run([sys.executable, str(pipeline_script)])
    else:
        console.print("[yellow]Pipeline script not found. Run manually:[/yellow]")
        console.print("  python scripts/ingest.py")
        console.print("  python scripts/transcribe.py")
        console.print("  python scripts/analyze.py")
        console.print("  python scripts/edit.py")
        console.print("  python scripts/upload.py")

def watch_mode(config, check_interval=5):
    """
    Continuously watch for SD card insertion.

    Args:
        config: Configuration dictionary
        check_interval: Seconds between checks
    """
    console.print(Panel.fit(
        f"[bold blue]Watching for SD card:[/bold blue] {config['sd_card']['volume_name']}\n"
        f"Checking every {check_interval} seconds...\n"
        "Press Ctrl+C to stop",
        title="SD Card Watcher"
    ))

    was_mounted = False

    try:
        while True:
            is_mounted = check_sd_card(config)

            if is_mounted and not was_mounted:
                # SD card just inserted
                console.print(f"\n[green]✓[/green] SD card mounted at {datetime.now().strftime('%H:%M:%S')}")

                if has_new_footage(config):
                    footage_path = get_footage_path(config)
                    files = list(footage_path.glob("*.[mM][pPoOvV][4vV]"))
                    console.print(f"  Found {len(files)} video file(s)")
                    trigger_workflow()
                else:
                    console.print("  [yellow]No video files found[/yellow]")

            elif not is_mounted and was_mounted:
                # SD card just removed
                console.print(f"\n[red]✗[/red] SD card removed at {datetime.now().strftime('%H:%M:%S')}")

            was_mounted = is_mounted
            time.sleep(check_interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher stopped[/yellow]")

def main():
    """Main entry point"""
    config = load_config()

    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            # Single check mode
            if check_sd_card(config):
                console.print("[green]✓[/green] SD card is mounted")
                if has_new_footage(config):
                    console.print("[green]✓[/green] Video files found")
                else:
                    console.print("[yellow]![/yellow] No video files found")
            else:
                console.print("[red]✗[/red] SD card not mounted")
            return

        elif sys.argv[1] == "--trigger":
            # Manual trigger mode
            trigger_workflow()
            return

    # Default: watch mode
    watch_mode(config)

if __name__ == "__main__":
    main()
