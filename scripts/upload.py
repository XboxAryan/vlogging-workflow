#!/usr/bin/env python3
"""
YouTube Upload Script
=====================
Uploads the final video to YouTube as a private video.
"""

import os
import sys
import json
import pickle
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# YouTube API scopes
# youtube.upload for video uploads, youtube for thumbnail setting
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube'
]

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_authenticated_service(config):
    """Get authenticated YouTube service"""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        console.print("[red]Error: Google API libraries not installed.[/red]")
        console.print("Run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
        return None

    base_dir = Path(config['project']['base_dir'])
    credentials_path = base_dir / "credentials.json"
    token_path = base_dir / "token.pickle"

    credentials = None

    # Load existing token
    if token_path.exists():
        with open(token_path, 'rb') as token:
            credentials = pickle.load(token)

    # Refresh or get new credentials
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not credentials_path.exists():
                console.print(f"[red]Error: credentials.json not found at {credentials_path}[/red]")
                console.print("\nTo set up YouTube API:")
                console.print("1. Go to https://console.cloud.google.com/")
                console.print("2. Create a project and enable YouTube Data API v3")
                console.print("3. Create OAuth 2.0 credentials (Desktop application)")
                console.print("4. Download credentials.json and place in project root")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES)
            credentials = flow.run_local_server(port=0)

        # Save token for future use
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)

    return build('youtube', 'v3', credentials=credentials)

def upload_video(youtube, video_path, title, description, tags, category_id, privacy_status):
    """Upload video to YouTube"""
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=50*1024*1024,  # 50 MB chunks for faster uploads
        resumable=True,
        mimetype='video/mp4'
    )

    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )

    response = None
    file_size = os.path.getsize(video_path)

    console.print(f"\n[bold]Uploading to YouTube...[/bold]")
    console.print(f"  File size: {file_size/1024/1024:.1f} MB")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Uploading...", total=None)

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    percent = int(status.progress() * 100)
                    progress.update(task, description=f"Uploading... {percent}%")
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    console.print(f"  [yellow]Temporary error, retrying...[/yellow]")
                    continue
                raise

    return response


def set_video_thumbnail(youtube, video_id: str, thumbnail_path: str) -> bool:
    """
    Set a custom thumbnail for a YouTube video.

    Note: Requires a verified YouTube account. Unverified accounts will fail.

    Args:
        youtube: Authenticated YouTube service
        video_id: YouTube video ID
        thumbnail_path: Path to the thumbnail image (JPEG, PNG, GIF, BMP)

    Returns:
        True if successful, False otherwise
    """
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    try:
        media = MediaFileUpload(
            thumbnail_path,
            mimetype='image/jpeg'  # YouTube accepts JPEG, PNG, GIF, BMP
        )

        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media
        ).execute()

        return True
    except HttpError as e:
        if e.resp.status == 403:
            console.print("[yellow]Note: Custom thumbnails require a verified YouTube account.[/yellow]")
            console.print("[dim]You can set the thumbnail manually in YouTube Studio.[/dim]")
        else:
            console.print(f"[yellow]Could not set thumbnail: {e}[/yellow]")
        return False
    except Exception as e:
        console.print(f"[yellow]Could not set thumbnail: {e}[/yellow]")
        return False


def upload_video_direct(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy_status: str,
    thumbnail_path: str = None,
    made_for_kids: bool = False,
) -> dict:
    """
    Upload a video directly to YouTube with optional custom thumbnail.

    Args:
        youtube: Authenticated YouTube service
        video_path: Path to the video file
        title: Video title
        description: Video description
        tags: List of tags
        category_id: YouTube category ID
        privacy_status: private, unlisted, or public
        thumbnail_path: Optional path to custom thumbnail image
        made_for_kids: Whether the video is made for kids

    Returns:
        Dict with 'success', 'video_id', 'video_url', 'error' keys
    """
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    result = {
        'success': False,
        'video_id': None,
        'video_url': None,
        'error': None,
    }

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': made_for_kids
        }
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=50*1024*1024,  # 50 MB chunks for faster uploads
        resumable=True,
        mimetype='video/mp4'
    )

    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )

    try:
        response = None
        file_size = os.path.getsize(video_path)

        console.print(f"\n[bold]Uploading: {Path(video_path).name}[/bold]")
        console.print(f"  File size: {file_size/1024/1024:.1f} MB")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Uploading...", total=None)

            while response is None:
                try:
                    status, response = request.next_chunk()
                    if status:
                        percent = int(status.progress() * 100)
                        progress.update(task, description=f"Uploading... {percent}%")
                except HttpError as e:
                    if e.resp.status in [500, 502, 503, 504]:
                        console.print(f"  [yellow]Temporary error, retrying...[/yellow]")
                        continue
                    raise

        video_id = response['id']
        video_url = f"https://youtube.com/watch?v={video_id}"

        result['success'] = True
        result['video_id'] = video_id
        result['video_url'] = video_url

        console.print(f"  [green]Upload complete: {video_url}[/green]")

        # Set custom thumbnail if provided
        if thumbnail_path and Path(thumbnail_path).exists():
            console.print("  [dim]Setting custom thumbnail...[/dim]")
            if set_video_thumbnail(youtube, video_id, thumbnail_path):
                console.print("  [green]Thumbnail set successfully[/green]")

        return result

    except HttpError as e:
        result['error'] = f"YouTube API error: {e}"
        console.print(f"[red]Upload failed: {e}[/red]")
        return result
    except Exception as e:
        result['error'] = str(e)
        console.print(f"[red]Upload failed: {e}[/red]")
        return result


def process_project(project_path, config, skip_upload=False):
    """
    Upload video for a project.

    Args:
        project_path: Path to project directory
        config: Configuration dictionary
        skip_upload: If True, only show what would be uploaded

    Returns:
        YouTube video ID
    """
    project_path = Path(project_path)

    # Load manifest
    manifest_path = project_path / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]Error: No manifest.json found in {project_path}[/red]")
        return None

    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    project_date = manifest.get('date', datetime.now().strftime('%Y-%m-%d'))

    # Check for output video
    output_info = manifest.get('output', {})
    video_path = output_info.get('path')

    if not video_path:
        # Try to find video in output directory
        output_dir = project_path / "output"
        videos = list(output_dir.glob("vlog_*.mp4"))
        if videos:
            video_path = str(videos[0])
        else:
            console.print(f"[red]Error: No output video found. Run edit.py first.[/red]")
            return None

    video_path = Path(video_path)
    if not video_path.exists():
        console.print(f"[red]Error: Video file not found: {video_path}[/red]")
        return None

    # Load analysis for summary
    analysis_path = project_path / "analysis.json"
    summary = ""
    themes = []
    if analysis_path.exists():
        with open(analysis_path, 'r') as f:
            analysis = json.load(f)
            summary = analysis.get('summary', '')
            themes = analysis.get('themes', [])

    # Build title and description
    title_template = config['youtube']['title_template']
    title = title_template.replace('{date}', project_date)

    description = config['youtube']['description']
    if summary:
        description = f"{summary}\n\n{description}"

    tags = config['youtube']['tags'].copy()
    tags.extend(themes)

    console.print(Panel.fit(
        f"[bold]Upload Details[/bold]\n\n"
        f"Title: {title}\n"
        f"Description: {description[:100]}...\n"
        f"Tags: {', '.join(tags)}\n"
        f"Privacy: {config['youtube']['privacy_status']}\n"
        f"Video: {video_path.name}",
        title="YouTube Upload"
    ))

    if skip_upload:
        console.print("\n[yellow]Dry run - skipping actual upload[/yellow]")
        return None

    # Get YouTube service
    youtube = get_authenticated_service(config)
    if youtube is None:
        return None

    try:
        response = upload_video(
            youtube,
            video_path,
            title,
            description,
            tags,
            config['youtube']['category_id'],
            config['youtube']['privacy_status']
        )

        video_id = response['id']
        video_url = f"https://youtube.com/watch?v={video_id}"

        console.print(Panel.fit(
            f"[bold green]Upload complete![/bold green]\n\n"
            f"Video ID: {video_id}\n"
            f"URL: {video_url}",
            title="Success"
        ))

        # Update manifest with upload info
        manifest['upload'] = {
            'video_id': video_id,
            'url': video_url,
            'uploaded_at': datetime.now().isoformat(),
            'title': title,
            'privacy_status': config['youtube']['privacy_status']
        }
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        return video_id

    except Exception as e:
        console.print(f"[red]Error uploading video: {e}[/red]")
        return None

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

    skip_upload = "--dry-run" in sys.argv

    # Check for project path argument
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args:
        project_path = Path(args[0])
    else:
        project_path = find_latest_project(config)

    if not project_path or not project_path.exists():
        console.print("[red]Error: No project found. Run previous steps first.[/red]")
        return

    result = process_project(project_path, config, skip_upload=skip_upload)

    if result:
        print(f"\nVIDEO_ID={result}")
        print(f"VIDEO_URL=https://youtube.com/watch?v={result}")

if __name__ == "__main__":
    main()
