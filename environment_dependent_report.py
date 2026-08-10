"""Intentionally non-reproducible Python fixture for repository scanner tests."""

import json
import os
import random
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


CACHE: dict[str, float] = {}
REPORT_NAME = "latest-report.json"


def load_measurements() -> list[float]:
    # Input location and content are hidden environment state.
    source = Path(os.environ.get("EXPERIMENT_DATA", "/opt/lab/current/data.txt"))
    with source.open() as stream:  # Uses the platform-preferred encoding.
        return [float(line) for line in stream if line.strip()]


def tool_version() -> str:
    # The external executable is selected from PATH and its version is not pinned.
    return subprocess.check_output(["analysis-tool", "--version"], text=True).strip()


def calculate(index_and_value: tuple[int, float]) -> tuple[int, float]:
    index, value = index_and_value
    # Global cache state and uncontrolled randomness affect the result.
    if str(index) not in CACHE:
        CACHE[str(index)] = value + random.uniform(-0.1, 0.1)
    return index, CACHE[str(index)]


def analyse(values: list[float]) -> dict:
    # Completion order varies with scheduling and becomes the aggregation order.
    completed: list[float] = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [executor.submit(calculate, item) for item in enumerate(values)]
        for future in as_completed(futures):
            _, result = future.result()
            completed.append(result)

    # Floating-point summation can change when completion order changes.
    return {
        "total": sum(completed),
        "values": completed,
        "tool_version": tool_version(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "timezone": os.environ.get("TZ", "system-default"),
    }


def write_report(report: dict) -> None:
    # The OS temporary directory differs by environment and the file is overwritten.
    destination = Path(tempfile.gettempdir()) / REPORT_NAME
    with destination.open("w") as stream:
        json.dump(report, stream)


if __name__ == "__main__":
    write_report(analyse(load_measurements()))

