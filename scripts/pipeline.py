#!/usr/bin/env python3
"""
Main Pipeline Script
====================
Orchestrates the complete vlogging workflow from ingestion to upload.
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def run_step(script_name, args=None, description=None):
    """Run a pipeline step"""
    scripts_dir = Path(__file__).parent
    script_path = scripts_dir / script_name

    if description:
        console.print(f"\n{'='*60}")
        console.print(f"[bold blue]Step: {description}[/bold blue]")
        console.print('='*60)

    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    result = subprocess.run(cmd)
    return result.returncode == 0

def main():
    """Run the complete pipeline"""
    config = load_config()

    console.print(Panel.fit(
        "[bold]Vlogging Workflow Pipeline[/bold]\n\n"
        "This will run the complete workflow:\n"
        "1. Ingest footage from SD card\n"
        "2. Transcribe audio using Whisper\n"
        "3. Analyze and select highlights with AI\n"
        "4. Edit video with FFmpeg\n"
        "5. Upload to YouTube (private)",
        title="Vlog Pipeline"
    ))

    # Check for flags
    auto_mode = "--auto" in sys.argv
    skip_upload = "--skip-upload" in sys.argv
    use_fallback = "--fallback" in sys.argv

    if not auto_mode:
        if not Confirm.ask("\nProceed with pipeline?"):
            console.print("[yellow]Pipeline cancelled[/yellow]")
            return

    start_time = datetime.now()

    # Step 1: Ingest
    if not run_step("ingest.py", description="Ingesting footage"):
        console.print("[red]Ingestion failed. Stopping pipeline.[/red]")
        return

    # Step 2: Transcribe
    if not run_step("transcribe.py", description="Transcribing audio"):
        console.print("[red]Transcription failed. Stopping pipeline.[/red]")
        return

    # Step 3: Analyze
    analyze_args = ["--fallback"] if use_fallback else []
    if not run_step("analyze.py", args=analyze_args, description="Analyzing content"):
        console.print("[red]Analysis failed. Stopping pipeline.[/red]")
        return

    # Step 4: Edit
    if not run_step("edit.py", description="Editing video"):
        console.print("[red]Editing failed. Stopping pipeline.[/red]")
        return

    # Step 5: Upload
    if not skip_upload:
        if not run_step("upload.py", description="Uploading to YouTube"):
            console.print("[yellow]Upload failed. Video saved locally.[/yellow]")
    else:
        console.print("\n[yellow]Skipping upload (--skip-upload flag)[/yellow]")

    # Summary
    elapsed = datetime.now() - start_time

    console.print(Panel.fit(
        f"[bold green]Pipeline Complete![/bold green]\n\n"
        f"Total time: {elapsed.total_seconds()/60:.1f} minutes\n\n"
        f"Check the projects folder for your video.",
        title="Done"
    ))

if __name__ == "__main__":
    main()
