"""Bounded subprocess execution for CPU-heavy document work."""

from __future__ import annotations

import multiprocessing as mp
from collections.abc import Callable
from queue import Empty
from typing import Any


class ProcessWorkerTimeoutError(TimeoutError):
    pass


class ProcessWorkerError(RuntimeError):
    pass


def run_in_process(
    target: Callable[..., Any],
    *,
    timeout_seconds: float,
    kwargs: dict[str, Any],
) -> Any:
    """Run a pickleable function in a killable child process.

    ``ProcessPoolExecutor`` cannot safely kill one wedged pypdf task.  A dedicated
    child lets the worker enforce a real timeout on Windows and Unix alike.
    """

    context = mp.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(target=_run_target, args=(target, kwargs, result_queue))
    process.start()
    try:
        state, payload = result_queue.get(timeout=timeout_seconds)
    except Empty as exc:
        if process.is_alive():
            process.terminate()
        process.join()
        result_queue.close()
        result_queue.join_thread()
        raise ProcessWorkerTimeoutError(
            f"PDF processing timed out after {timeout_seconds:.0f} seconds."
        ) from exc
    process.join()
    result_queue.close()
    result_queue.join_thread()
    if state == "error":
        raise ProcessWorkerError(payload)
    return payload


def _run_target(target: Callable[..., Any], kwargs: dict[str, Any], result_queue: Any) -> None:
    try:
        result_queue.put(("ok", target(**kwargs)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))
