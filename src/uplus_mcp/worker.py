"""Dedicated worker thread that owns the Playwright sync session.

Why this exists
---------------
``mcp.server.fastmcp`` executes *sync* tool functions directly on the asyncio
event-loop thread (see ``call_fn_with_arg_validation``). Playwright's sync API
refuses to run when an event loop is already running in the current thread:

    "It looks like you are using Playwright Sync API inside the asyncio loop."

The fix is not ``asyncio.to_thread`` per call. Playwright sync objects are
strictly thread-affine: the ``Browser``/``Context``/``Page`` created by
``sync_playwright().start()`` may only be touched from the OS thread that ran
that call. Handing individual calls to a thread pool therefore breaks the
session.

So: exactly one long-lived worker thread owns the session. Every operation is
submitted to that thread's queue, executed there, and its result is handed back.
Async MCP tools await the completion event without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import traceback
from typing import Any, Callable


class _Task:
    __slots__ = ("fn", "args", "kwargs", "done", "value", "error", "traceback")

    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.traceback: str = ""


class BrowserThread:
    """One persistent thread that owns every Playwright object."""

    def __init__(self, name: str = "uplus-browser") -> None:
        self._name = name
        self._queue: "queue.Queue[_Task]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._serve, name=self._name, daemon=True
            )
            self._thread.start()

    def _serve(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:  # shutdown sentinel
                return
            try:
                task.value = task.fn(*task.args, **task.kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised on caller side
                task.error = exc
                task.traceback = traceback.format_exc()  # type: ignore[attr-defined]
            finally:
                task.done.set()

    # ------------------------------------------------------------------ #
    # submission
    # ------------------------------------------------------------------ #
    def run_sync(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Block the calling thread until ``fn`` finishes on the worker thread."""
        self._ensure_thread()
        task = _Task(fn, args, kwargs)
        self._queue.put(task)
        task.done.wait()
        if task.error is not None:
            raise task.error
        return task.value

    async def run_async(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Await ``fn`` executed on the worker thread, without blocking the loop.

        ``run_in_executor`` blocks that pool thread; it is only a temporary
        waiter. The Playwright work itself always happens on the worker thread.
        """
        self._ensure_thread()
        task = _Task(fn, args, kwargs)
        self._queue.put(task)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, task.done.wait)
        if task.error is not None:
            raise task.error
        return task.value

    def shutdown(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
