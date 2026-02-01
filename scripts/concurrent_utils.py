"""
Concurrent Processing Utilities
===============================
Shared utilities for parallel processing across the vlogging workflow.
Provides hardware-aware worker defaults and unified error handling.
"""

import os
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import TypeVar, Callable, Iterable, Optional, List, Any
from dataclasses import dataclass
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

T = TypeVar('T')
R = TypeVar('R')

# Hardware-aware defaults
CPU_COUNT = os.cpu_count() or 4
FFMPEG_WORKERS = min(4, max(1, CPU_COUNT - 2))  # Leave cores for system, cap at 4 for hw encoder
FFPROBE_WORKERS = min(6, max(1, CPU_COUNT - 1))
WHISPER_WORKERS = min(2, max(1, CPU_COUNT // 4))  # Memory-intensive
HASH_WORKERS = min(4, max(1, CPU_COUNT - 2))


@dataclass
class TaskResult:
    """Result wrapper for parallel task execution."""
    input_item: Any
    result: Any
    error: Optional[Exception] = None

    @property
    def success(self) -> bool:
        return self.error is None


def get_worker_count(config: dict, task_type: str) -> int:
    """Get worker count from config or use hardware-aware defaults."""
    concurrency = config.get('concurrency', {})

    if not concurrency.get('enabled', True):
        return 1  # Sequential fallback

    workers = concurrency.get('workers', {})
    defaults = {
        'ffprobe': FFPROBE_WORKERS,
        'ffmpeg': FFMPEG_WORKERS,
        'whisper': WHISPER_WORKERS,
        'hash': HASH_WORKERS,
    }

    configured = workers.get(task_type)
    if configured is not None:
        return max(1, configured)

    return defaults.get(task_type, max(1, CPU_COUNT - 2))


def is_concurrency_enabled(config: dict) -> bool:
    """Check if concurrency is enabled in config."""
    return config.get('concurrency', {}).get('enabled', True)


def parallel_map(
    func: Callable[[T], R],
    items: Iterable[T],
    max_workers: int,
    description: str = "Processing",
    console: Optional[Console] = None,
    use_process_pool: bool = False,
    show_progress: bool = True
) -> List[TaskResult]:
    """
    Execute function in parallel across items with progress tracking.

    Args:
        func: Function to apply to each item
        items: Iterable of items to process
        max_workers: Maximum concurrent workers
        description: Progress bar description
        console: Rich console for output
        use_process_pool: Use ProcessPoolExecutor instead of ThreadPoolExecutor
        show_progress: Whether to display progress bar

    Returns:
        List of TaskResult objects preserving input order
    """
    items_list = list(items)
    if not items_list:
        return []

    results: List[Optional[TaskResult]] = [None] * len(items_list)

    # Force sequential if only 1 worker
    if max_workers <= 1:
        for idx, item in enumerate(items_list):
            try:
                result = func(item)
                results[idx] = TaskResult(input_item=item, result=result)
            except Exception as e:
                results[idx] = TaskResult(input_item=item, result=None, error=e)
        return results  # type: ignore

    Executor = ProcessPoolExecutor if use_process_pool else ThreadPoolExecutor

    with Executor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_idx = {
            executor.submit(func, item): idx
            for idx, item in enumerate(items_list)
        }

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task(description, total=len(items_list))

                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        result = future.result()
                        results[idx] = TaskResult(
                            input_item=items_list[idx],
                            result=result
                        )
                    except Exception as e:
                        results[idx] = TaskResult(
                            input_item=items_list[idx],
                            result=None,
                            error=e
                        )
                    progress.update(task, advance=1)
        else:
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results[idx] = TaskResult(
                        input_item=items_list[idx],
                        result=result
                    )
                except Exception as e:
                    results[idx] = TaskResult(
                        input_item=items_list[idx],
                        result=None,
                        error=e
                    )

    return results  # type: ignore


def parallel_map_with_fallback(
    func: Callable[[T], R],
    items: Iterable[T],
    max_workers: int,
    description: str = "Processing",
    console: Optional[Console] = None
) -> List[TaskResult]:
    """
    Try parallel execution, fall back to sequential on critical failure.
    """
    try:
        return parallel_map(func, items, max_workers, description, console)
    except Exception as e:
        if console:
            console.print(f"[yellow]Parallel execution failed: {e}. Falling back to sequential.[/yellow]")

        items_list = list(items)
        results = []
        for item in items_list:
            try:
                results.append(TaskResult(item, func(item)))
            except Exception as err:
                results.append(TaskResult(item, None, err))
        return results


def report_failures(results: List[TaskResult], console: Console, max_show: int = 5) -> None:
    """Report failures from parallel operation results."""
    failures = [r for r in results if not r.success]
    if not failures:
        return

    console.print(f"[yellow]Warning: {len(failures)} item(s) failed:[/yellow]")
    for f in failures[:max_show]:
        console.print(f"  - {f.input_item}: {f.error}")

    if len(failures) > max_show:
        console.print(f"  ... and {len(failures) - max_show} more")
