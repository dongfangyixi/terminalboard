"""Shared, backend-agnostic data model for scalar series.

Both the default (tensorboard) and ``--light`` (pure-Python) readers produce
these same structures, so the renderers never need to know which parser ran.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ScalarSeries:
    """A single scalar tag's time-series within one run."""

    tag: str
    steps: List[int] = field(default_factory=list)
    values: List[float] = field(default_factory=list)
    wall_times: List[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.steps)

    def append(self, step: int, value: float, wall_time: float = 0.0) -> None:
        self.steps.append(step)
        self.values.append(value)
        self.wall_times.append(wall_time)


@dataclass
class Run:
    """One TensorBoard run: a directory's worth of event files."""

    name: str  # path relative to the logdir (or "." for the logdir itself)
    path: str  # absolute directory path
    series: Dict[str, ScalarSeries] = field(default_factory=dict)

    def tags(self) -> List[str]:
        return sorted(self.series.keys())

    def get(self, tag: str) -> ScalarSeries:
        s = self.series.get(tag)
        if s is None:
            s = ScalarSeries(tag)
            self.series[tag] = s
        return s
