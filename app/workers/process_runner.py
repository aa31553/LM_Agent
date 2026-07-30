"""Bounded subprocess execution for CPU-heavy document work."""

from __future__ import annotations

import multiprocessing as mp
import time
from collections.abc import Callable
from queue import Empty
from typing import Any


class ProcessWorkerTimeoutError(TimeoutError):
    pass


class ProcessWorkerError(RuntimeError):
    pass


class ProcessWorkerCancelledError(RuntimeError):
    pass


def run_in_process(
    target: Callable[..., Any],
    *,
    timeout_seconds: float,
    kwargs: dict[str, Any],
    cancel_check: Callable[[], bool] | None = None,
    poll_interval_seconds: float = 0.25,
) -> Any:
    """Run a pickleable function in a killable child process.

    ``ProcessPoolExecutor`` cannot safely kill one wedged pypdf task.  A dedicated
    child lets the worker enforce a real timeout on Windows and Unix alike.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero")

    context = mp.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(target=_run_target, args=(target, kwargs, result_queue))
    process.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessWorkerTimeoutError(
                    f"Processing timed out after {timeout_seconds:.0f} seconds."
                )
            try:
                state, payload = result_queue.get(
                    timeout=min(poll_interval_seconds, remaining)
                )
                break
            except Empty:
                if cancel_check is not None and cancel_check():
                    raise ProcessWorkerCancelledError(
                        "Processing was cancelled."
                    )
    except BaseException:
        if process.is_alive():
            process.terminate()
        process.join()
        raise
    else:
        process.join()
    finally:
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
