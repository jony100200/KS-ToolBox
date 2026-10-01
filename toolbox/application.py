"""Application-scoped services explicitly injected by the shell."""
from __future__ import annotations

from dataclasses import dataclass

from toolbox.job_queue import JobQueue


@dataclass(frozen=True)
class AppServices:
    queue: JobQueue


def create_services() -> AppServices:
    return AppServices(queue=JobQueue())
