"""Cooperative execution controls at complete ReAct turn boundaries."""

import threading


class RunCancelled(Exception):
    """Internal cancellation signal; does not interrupt an in-flight request."""


class RunControl:
    def __init__(self, *, paused: bool = False):
        self._condition = threading.Condition()
        self._paused = paused
        self._credits = 0
        self._active = False
        self._cancelled = False

    @property
    def state(self) -> str:
        with self._condition:
            if self._cancelled:
                return "cancelling"
            if self._paused and not self._credits:
                return "pausing" if self._active else "paused"
            return "running"

    def before_turn(self) -> None:
        with self._condition:
            self._active = False
            while self._paused and not self._credits and not self._cancelled:
                self._condition.wait()
            self.check_cancelled()
            if self._credits:
                self._credits -= 1
            self._active = True

    def check_cancelled(self) -> None:
        with self._condition:
            if self._cancelled:
                raise RunCancelled()

    def command(self, action: str) -> None:
        with self._condition:
            if action == "pause":
                self._paused, self._credits = True, 0
            elif action == "resume":
                self._paused, self._credits = False, 0
            elif action == "step":
                if not self._paused or self._active or self._credits:
                    raise ValueError("Pause at a turn boundary before stepping.")
                self._credits = 1
            elif action == "cancel":
                self._cancelled = True
            else:
                raise ValueError("Unknown run control command.")
            self._condition.notify_all()
