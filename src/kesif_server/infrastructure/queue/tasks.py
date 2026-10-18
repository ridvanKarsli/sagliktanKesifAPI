"""RQ görev giriş noktası. Worker sürecinde içe aktarılır; bileşim kökünü tembel kurar."""

from __future__ import annotations

from ...container import get_container


def execute_run(run_id: str) -> str:
    container = get_container()
    run = container.execute_run(run_id)
    return run.status.value
