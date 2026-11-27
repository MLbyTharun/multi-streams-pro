"""Linear, autonomous task progression driven by DONE / NOT_DONE model verdicts."""

from __future__ import annotations

import re

import scene_config

_VERDICT = re.compile(r"^(NOT[ _-]?DONE|DONE)(?![\w-])")


def flatten_tasks(context: dict) -> list[dict]:
    """Topics then tasks, in the order supplied by the backend."""
    return [
        {"topic": topic["title"], "title": task["title"], "instruction": task["instruction"]}
        for topic in context["topics"] for task in topic["tasks"]
    ]


def parse_verdict(raw: str) -> str:
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    first = lines[0].strip().strip("*`#>:. ").upper() if lines else ""
    match = _VERDICT.match(first)
    if not match:
        raise ValueError("model did not answer DONE or NOT_DONE")
    return "DONE" if match.group(1) == "DONE" else "NOT_DONE"


class TaskProgress:
    def __init__(self, tasks: list[dict], confirmations: int = scene_config.TASK_DONE_CONFIRMATIONS):
        if not tasks:
            raise ValueError("task list is empty")
        if confirmations < 1:
            raise ValueError("confirmations must be at least 1")
        self.tasks = tasks
        self.confirmations = confirmations
        self.index = 0
        self.streak = 0
        self.finished = False

    def current(self) -> dict | None:
        return None if self.finished else self.tasks[self.index]

    def prompt(self, camera_count: int) -> str:
        task = self.current()
        if task is None:
            raise RuntimeError("all tasks are complete")
        following = self.tasks[self.index + 1] if self.index + 1 < len(self.tasks) else None
        return scene_config.TASK_PROGRESS_PROMPT.format(
            camera_count=camera_count, task_number=self.index + 1, task_total=len(self.tasks),
            topic=task["topic"], title=task["title"], instruction=task["instruction"],
            next_task=following["title"] if following else "none (this is the last task)",
        )

    def observe(self, verdict: str) -> dict:
        if self.finished:
            raise RuntimeError("all tasks are complete")
        completed = None
        if verdict != "DONE":
            self.streak = 0
            event = "in_progress"
        else:
            self.streak += 1
            if self.streak < self.confirmations:
                event = "confirming"
            else:
                completed = self.tasks[self.index]
                self.streak = 0
                if self.index + 1 >= len(self.tasks):
                    self.finished = True
                    event = "all_complete"
                else:
                    self.index += 1
                    event = "task_completed"
        return {
            "event": event, "completed": completed, "current": self.current(),
            "index": self.index, "total": len(self.tasks), "streak": self.streak,
        }

    def snapshot(self) -> dict:
        done = len(self.tasks) if self.finished else self.index
        current = self.current()
        return {
            "index": self.index, "total": len(self.tasks), "streak": self.streak,
            "finished": self.finished,
            "currentTitle": current["title"] if current else None,
            "completedTitles": [task["title"] for task in self.tasks[:done]],
        }
