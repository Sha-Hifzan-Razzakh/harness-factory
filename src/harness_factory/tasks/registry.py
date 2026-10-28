"""TaskRegistry (B1): versioned store of tasks read from catalog/tasks."""

from __future__ import annotations

from pathlib import Path

from harness_factory import settings
from harness_factory.tasks.model import Split, Task, load_task


class TaskRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.TASKS_DIR
        self._tasks: dict[str, Task] = {}
        for task_yaml in sorted(self.root.glob("*/task.yaml")):
            task = load_task(task_yaml.parent)
            if task.id in self._tasks:
                raise ValueError(f"duplicate task id {task.id}")
            self._tasks[task.id] = task

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError as e:
            raise KeyError(f"unknown task {task_id!r}; known: {sorted(self._tasks)}") from e

    def all(self, include_held_out: bool = False) -> list[Task]:
        return [t for t in self._tasks.values() if include_held_out or t.split != Split.HELD_OUT]

    def by_split(self, split: Split) -> list[Task]:
        return [t for t in self._tasks.values() if t.split == split]

    def __len__(self) -> int:
        return len(self._tasks)
