#!/usr/bin/env python3
"""
AI Analysis Script
==================
Uses AI to analyze transcripts and select highlights for the final video.
Supports: OpenRouter, OpenAI, Anthropic
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file if it exists
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def load_config():
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def clean_transcript_segment(text):
    """
    Clean a transcript segment to remove likely music/lyrics and explicit content.
    Returns cleaned text or None if segment should be skipped.
    """
    import re

    if not text:
        return None

    text = text.strip()

    # Skip very short segments (lowered from 5 to 2 to preserve more content)
    if len(text.split()) < 2:
        return None

    # List of explicit words to filter (partial list for safety)
    explicit_patterns = [
        r'\b(fuck|shit|bitch|ass|damn|hell|cock|dick|pussy)\b',
        r'\b(nigga|nigger)\b',
    ]

    # Check for explicit content
    text_lower = text.lower()
    for pattern in explicit_patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return None

    # Patterns that suggest music/lyrics rather than speech:
    # - Very short repetitive phrases
    # - Lines that look like song lyrics (rhyming patterns, etc.)
    # - Text that's mostly single words repeated

    # Skip if text looks like garbled music transcription
    words = text.split()
    unique_words = set(w.lower() for w in words)

    # If more than 60% of words are repeated, likely music
    if len(words) > 10 and len(unique_words) / len(words) < 0.4:
        return None

    # Skip very short duration high word count (likely fast music)
    # This is handled elsewhere but good to double-check

    return text

def build_prompt(transcript_data, config, max_segments=100, user_context=None):
    """Build the analysis prompt with optional user context."""
    target_duration = config['project']['target_duration_minutes'] * 60
    total_duration = sum(f.get('duration', 0) for f in transcript_data.get('files', []))

    # Build file duration map first (needed for explicit file list)
    files_info = transcript_data.get('files', [])
    file_durations = {f.get('file', ''): f.get('duration', 0) for f in files_info}

    # Filter and clean segments
    all_segments = transcript_data.get('all_segments', [])
    cleaned_segments = []
    for seg in all_segments:
        cleaned_text = clean_transcript_segment(seg.get('text', ''))
        if cleaned_text:
            cleaned_segments.append({
                **seg,
                'text': cleaned_text
            })

    # Calculate filtering ratio for sparse mode detection
    original_count = len(all_segments)
    cleaned_count = len(cleaned_segments)
    filter_ratio = (original_count - cleaned_count) / original_count if original_count > 0 else 0

    console.print(f"  Cleaned segments: {cleaned_count} of {original_count} ({filter_ratio*100:.0f}% filtered)")

    # Warn if most segments were filtered (sparse transcript)
    is_sparse = filter_ratio > 0.5
    if is_sparse:
        console.print(f"  [yellow]Warning: Sparse transcript detected ({filter_ratio*100:.0f}% filtered)[/yellow]")

    # Limit segments for context limits
    if len(cleaned_segments) > max_segments:
        step = len(cleaned_segments) // max_segments
        cleaned_segments = cleaned_segments[::step][:max_segments]

    # Group segments by file for better context
    from collections import defaultdict
    by_file = defaultdict(list)
    for seg in cleaned_segments:
        by_file[seg['file']].append(seg)

    # Build EXPLICIT FILE LIST header (always show all selected files)
    file_list_header = "SELECTED FILES (you MUST use ALL of these):\n"
    for f in files_info:
        filename = f.get('file', '')
        duration = f.get('duration', 0)
        file_list_header += f"- {filename}: {duration:.0f}s total\n"
    file_list_header += f"\nTotal source footage: {total_duration:.0f}s → Target output: {target_duration}s\n"

    # Build segments text grouped by file
    segments_text = ""
    for filename in sorted(by_file.keys()):
        file_dur = file_durations.get(filename, 0)
        segments_text += f"\n=== {filename} (total: {file_dur:.0f}s) ===\n"
        for seg in sorted(by_file[filename], key=lambda x: x['start']):
            segments_text += f"  {seg['start']:.1f}-{seg['end']:.1f}: {seg['text']}\n"

    # Add files with no transcript segments (ensure all files appear)
    files_with_segments = set(by_file.keys())
    for f in files_info:
        filename = f.get('file', '')
        if filename not in files_with_segments:
            file_dur = f.get('duration', 0)
            segments_text += f"\n=== {filename} (total: {file_dur:.0f}s) ===\n"
            segments_text += f"  (No transcript segments - audio unclear or music)\n"

    # Build user context section if provided
    context_section = ""
    if user_context:
        context_parts = []
        if user_context.get('title'):
            context_parts.append(f"- Title: {user_context['title']}")
        if user_context.get('description'):
            context_parts.append(f"- Description: {user_context['description']}")
        if user_context.get('instructions'):
            context_parts.append(f"- Creator Instructions: {user_context['instructions']}")
        if user_context.get('tone'):
            tone_guidance = {
                'calm': 'Keep a relaxed, peaceful pace. Prioritize quiet, contemplative moments.',
                'energetic': 'Keep it fast-paced and exciting. Prioritize action and movement.',
                'informative': 'Focus on clear explanations and demonstrations.',
                'funny': 'Prioritize comedic moments, reactions, and humor.',
                'cinematic': 'Focus on visually interesting shots and story-driven moments.',
            }
            tone = user_context['tone']
            if tone in tone_guidance:
                context_parts.append(f"- Tone: {tone} - {tone_guidance[tone]}")

        if context_parts:
            context_section = f"""
VIDEO CONTEXT FROM CREATOR:
{chr(10).join(context_parts)}

Use this context to guide your clip selection. Prioritize segments that match the creator's vision.

"""

    # Add sparse mode guidance if transcript is sparse
    sparse_guidance = ""
    if is_sparse:
        sparse_guidance = """
SPARSE TRANSCRIPT NOTE:
The transcript data is sparse (poor audio quality or background music). You should:
- Still include clips from EVERY file listed above
- Use your judgment to select visually interesting portions
- Distribute time proportionally across all files
- When in doubt, prefer the middle portion of each clip

"""

    # Calculate proportional distribution hint
    num_files = len(files_info)
    time_per_file = target_duration / num_files if num_files > 0 else target_duration
    distribution_hint = f"Suggested distribution: ~{time_per_file:.0f}s per file (adjust based on content quality)"

    return f"""Create clips for a {target_duration}-second daily vlog from {total_duration:.0f}s of footage.

{file_list_header}
{context_section}{sparse_guidance}TRANSCRIPT BY FILE:
{segments_text}

INSTRUCTIONS:
- Target EXACTLY {target_duration} seconds total (sum of all clip durations)
- CRITICAL: Include content from EVERY selected file listed above. {distribution_hint}
- You can define ANY start/end times within each file's duration
- EXTEND clips beyond transcript boundaries to capture full moments (e.g., if interesting speech is at 12.4-15.1, you might clip 10.0-20.0)
- MERGE adjacent interesting segments into single longer clips (20-60 seconds each is ideal)
- Prioritize: genuine moments, humor, activities, conversations over silence/music
- Aim for 10-20 clips that together total {target_duration} seconds

Respond with ONLY valid JSON:
{{"summary":"one sentence","themes":["theme1","theme2"],"segments":[{{"f":"FILENAME.MP4","s":0.0,"e":30.0}}]}}"""

def repair_truncated_json(json_str):
    """Attempt to repair truncated JSON by closing open brackets"""
    import re

    # Count open/close brackets
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')

    # If we have unclosed structures, try to close them
    if open_braces > 0 or open_brackets > 0:
        # Find last complete segment entry
        # Look for pattern like {"f":"...", "s":..., "e":...}
        last_complete = json_str.rfind('}')
        if last_complete > 0:
            # Check if we're in the middle of an object
            after_last = json_str[last_complete+1:].strip()
            if after_last.startswith(','):
                # Truncated after a comma - remove trailing comma
                json_str = json_str[:last_complete+1]

        # Close remaining brackets
        json_str = json_str.rstrip(', \n\t')
        json_str += ']' * open_brackets + '}' * open_braces

    return json_str

def parse_ai_response(response_text):
    """Parse JSON from AI response, handling various formats"""
    # Handle markdown code blocks
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        parts = response_text.split("```")
        if len(parts) >= 2:
            response_text = parts[1]

    # Try to find JSON object in response
    response_text = response_text.strip()

    # Handle DeepSeek R1's <think> tags
    if "<think>" in response_text:
        if "</think>" in response_text:
            response_text = response_text.split("</think>")[-1].strip()

    # Find JSON object
    start = response_text.find('{')
    end = response_text.rfind('}')
    if start != -1 and end != -1:
        response_text = response_text[start:end+1]
    elif start != -1:
        # JSON might be truncated - try to repair
        response_text = repair_truncated_json(response_text[start:])

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Try repairing
        repaired = repair_truncated_json(response_text)
        return json.loads(repaired)

def normalize_ai_response(analysis):
    """Convert short field names to standard format expected by rest of pipeline"""
    if analysis is None:
        return None

    # Handle short segment format: {"f": "file", "s": start, "e": end}
    segments = analysis.get('segments', analysis.get('selected_segments', []))
    normalized_segments = []

    for i, seg in enumerate(segments):
        normalized = {
            'file': seg.get('f', seg.get('file', '')),
            'start': seg.get('s', seg.get('start', 0)),
            'end': seg.get('e', seg.get('end', 0)),
            'suggested_order': seg.get('suggested_order', i + 1),
            'reason': seg.get('reason', 'AI selected')
        }
        normalized_segments.append(normalized)

    return {
        'summary': analysis.get('summary', 'AI analysis'),
        'themes': analysis.get('themes', []),
        'selected_segments': normalized_segments,
        'estimated_duration': sum(s['end'] - s['start'] for s in normalized_segments)
    }

def analyze_with_openrouter(transcript_data, config, user_context=None):
    """Use OpenRouter API (OpenAI-compatible) for analysis"""
    try:
        from openai import OpenAI
    except ImportError:
        console.print("[red]Error: openai not installed. Run: pip install openai[/red]")
        return None

    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        console.print("[red]Error: OPENROUTER_API_KEY environment variable not set[/red]")
        console.print("Get a free key at: https://openrouter.ai/keys")
        return None

    base_url = config['ai'].get('base_url', 'https://openrouter.ai/api/v1')
    model = config['ai']['model']
    use_structured_output = config['ai'].get('structured_output', True)

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
    )

    prompt = build_prompt(transcript_data, config, user_context=user_context)

    console.print(f"  Sending to OpenRouter ({model})...")

    # JSON Schema for structured output
    response_schema = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One sentence summary of the vlog content"
            },
            "themes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of themes in the video"
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "f": {"type": "string", "description": "Filename"},
                        "s": {"type": "number", "description": "Start time in seconds"},
                        "e": {"type": "number", "description": "End time in seconds"}
                    },
                    "required": ["f", "s", "e"],
                    "additionalProperties": False
                },
                "description": "Selected video segments"
            }
        },
        "required": ["summary", "themes", "segments"],
        "additionalProperties": False
    }

    try:
        request_params = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 4096,
            "extra_headers": {
                "HTTP-Referer": "https://github.com/vlogging-workflow",
                "X-Title": "Vlogging Workflow"
            }
        }

        # Add structured output if enabled (not all models support this)
        if use_structured_output:
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "vlog_analysis",
                    "strict": True,
                    "schema": response_schema
                }
            }
            console.print("  Using structured JSON output...")

        response = client.chat.completions.create(**request_params)

        response_text = response.choices[0].message.content
        console.print(f"  Response length: {len(response_text)} chars")

        # With structured output, we can parse directly
        if use_structured_output:
            try:
                analysis = json.loads(response_text)
                return normalize_ai_response(analysis)
            except json.JSONDecodeError:
                console.print("[yellow]Structured output failed, falling back to parsing...[/yellow]")

        analysis = parse_ai_response(response_text)
        return normalize_ai_response(analysis)

    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing AI response: {e}[/red]")
        console.print(f"Response was: {response_text[:500]}...")
        return None
    except Exception as e:
        console.print(f"[red]Error calling OpenRouter API: {e}[/red]")
        return None

def analyze_with_anthropic(transcript_data, config, user_context=None):
    """Use Anthropic API for analysis"""
    try:
        import anthropic
    except ImportError:
        console.print("[red]Error: anthropic not installed. Run: pip install anthropic[/red]")
        return None

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY environment variable not set[/red]")
        return None

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(transcript_data, config, user_context=user_context)

    console.print(f"  Sending to Anthropic ({config['ai']['model']})...")

    try:
        message = client.messages.create(
            model=config['ai']['model'],
            max_tokens=4096,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        response_text = message.content[0].text
        console.print(f"  Response length: {len(response_text)} chars")
        analysis = parse_ai_response(response_text)
        return normalize_ai_response(analysis)

    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing AI response: {e}[/red]")
        console.print(f"Response was: {response_text[:500]}...")
        return None
    except Exception as e:
        console.print(f"[red]Error calling Anthropic API: {e}[/red]")
        return None

def analyze_with_ai(transcript_data, config, user_context=None):
    """Route to appropriate AI provider based on config"""
    provider = config['ai'].get('provider', 'openrouter')

    if provider == 'openrouter':
        return analyze_with_openrouter(transcript_data, config, user_context=user_context)
    elif provider == 'anthropic':
        return analyze_with_anthropic(transcript_data, config, user_context=user_context)
    elif provider == 'openai':
        # OpenAI uses same format as OpenRouter
        return analyze_with_openrouter(transcript_data, config, user_context=user_context)
    else:
        console.print(f"[red]Unknown AI provider: {provider}[/red]")
        return None

def fallback_analysis(transcript_data, config):
    """
    Simple fallback analysis when AI is not available.
    Selects segments based on audio activity (non-empty text).
    """
    target_duration = config['project']['target_duration_minutes'] * 60
    segments = transcript_data.get('all_segments', [])

    # Filter to segments with substantial text (likely talking)
    good_segments = [
        s for s in segments
        if len(s.get('text', '').split()) > 5  # At least 5 words
    ]

    # Sort by length of text (more talking = more interesting)
    good_segments.sort(key=lambda s: len(s.get('text', '')), reverse=True)

    selected = []
    total_time = 0

    for seg in good_segments:
        duration = seg['end'] - seg['start']
        if total_time + duration <= target_duration:
            selected.append({
                'file': seg['file'],
                'start': seg['start'],
                'end': seg['end'],
                'reason': 'Contains substantial speech',
                'suggested_order': len(selected) + 1
            })
            total_time += duration

    # Sort by file and timestamp for chronological order
    selected.sort(key=lambda s: (s['file'], s['start']))

    # Reassign order
    for i, seg in enumerate(selected):
        seg['suggested_order'] = i + 1

    return {
        'summary': f"Auto-selected segments from {transcript_data.get('project_date', 'unknown date')}",
        'themes': ['daily-life'],
        'selected_segments': selected,
        'narrative_notes': 'Segments selected based on speech activity, arranged chronologically',
        'estimated_duration': total_time
    }

def process_project(project_path, config, use_fallback=False, user_context=None):
    """
    Analyze a project's transcript and generate edit decisions.

    Args:
        project_path: Path to project directory
        config: Configuration dictionary
        use_fallback: If True, skip AI and use simple selection
        user_context: Optional dict with user-provided video context

    Returns:
        Analysis results
    """
    project_path = Path(project_path)
    transcript_dir = project_path / "transcripts"

    # Load combined transcript
    combined_path = transcript_dir / "combined.json"
    if not combined_path.exists():
        console.print(f"[red]Error: No combined transcript found. Run transcribe.py first.[/red]")
        return None

    with open(combined_path, 'r') as f:
        transcript_data = json.load(f)

    # Show user context if provided
    context_info = ""
    if user_context:
        context_info = f"\nContext: {user_context.get('title', 'Untitled')}"
        if user_context.get('instructions'):
            context_info += f"\nInstructions: {user_context.get('instructions')[:50]}..."

    console.print(Panel.fit(
        f"[bold]Analyzing project: {transcript_data.get('project_date', 'Unknown')}[/bold]\n"
        f"Total segments: {len(transcript_data.get('all_segments', []))}"
        f"{context_info}",
        title="AI Analysis"
    ))

    if use_fallback:
        console.print("  Using fallback analysis (no AI)...")
        analysis = fallback_analysis(transcript_data, config)
    else:
        analysis = analyze_with_ai(transcript_data, config, user_context=user_context)

        if analysis is None:
            console.print("  [yellow]Falling back to simple analysis...[/yellow]")
            analysis = fallback_analysis(transcript_data, config)

    if analysis is None:
        return None

    # Display results
    console.print(f"\n[bold]Summary:[/bold] {analysis.get('summary', 'N/A')}")
    console.print(f"[bold]Themes:[/bold] {', '.join(analysis.get('themes', []))}")

    table = Table(title="Selected Segments")
    table.add_column("#", style="cyan", width=4)
    table.add_column("File", style="green")
    table.add_column("Time", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Reason")

    total_selected = 0
    for seg in analysis.get('selected_segments', []):
        duration = seg['end'] - seg['start']
        total_selected += duration
        table.add_row(
            str(seg.get('suggested_order', '-')),
            seg['file'][:20],
            f"{seg['start']:.1f}-{seg['end']:.1f}",
            f"{duration:.1f}s",
            seg.get('reason', '')[:40]
        )

    console.print(table)
    console.print(f"\n[bold]Total selected:[/bold] {total_selected:.0f}s ({total_selected/60:.1f} min)")
    console.print(f"[bold]Target:[/bold] {config['project']['target_duration_minutes']} min")

    # Save analysis
    analysis['analyzed_at'] = datetime.now().isoformat()
    analysis_path = project_path / "analysis.json"
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2)

    console.print(Panel.fit(
        f"[bold green]Analysis complete![/bold green]\n"
        f"Output: {analysis_path}",
        title="Done"
    ))

    return analysis

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

    use_fallback = "--fallback" in sys.argv
    user_context = None

    # Check for --context flag
    if "--context" in sys.argv:
        context_idx = sys.argv.index("--context")
        if context_idx + 1 < len(sys.argv):
            context_path = Path(sys.argv[context_idx + 1])
            if context_path.exists():
                with open(context_path, 'r') as f:
                    user_context = json.load(f)
                console.print(f"[dim]Loaded user context from {context_path}[/dim]")

    # Check for project path argument (exclude flags and their values)
    args = []
    skip_next = False
    for i, a in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if a.startswith('--'):
            if a == "--context":
                skip_next = True
            continue
        args.append(a)

    if args:
        project_path = Path(args[0])
    else:
        project_path = find_latest_project(config)

    if not project_path or not project_path.exists():
        console.print("[red]Error: No project found. Run ingest.py and transcribe.py first.[/red]")
        return

    result = process_project(project_path, config, use_fallback=use_fallback, user_context=user_context)

    if result:
        print(f"\nPROJECT_PATH={project_path}")

if __name__ == "__main__":
    main()
