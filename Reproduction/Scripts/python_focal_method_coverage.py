#!/usr/bin/env python3
"""
Python focal-function coverage runner.

Reads Python_Dataset.json, extracts source functions and test code, executes
tests with pytest, and measures line and branch coverage with coverage.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_CANDIDATES = [
    SCRIPT_DIR / "data" / "Python_Dataset.json",
    SCRIPT_DIR / "Python_Dataset.json",
    Path("/content/Python_Dataset.json"),
    Path("/content/data/Python_Dataset.json"),
]
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"

GENERATED_FIELD_PATTERNS = (
    re.compile(r"^result_(\d+)_generated_unit_test$", re.IGNORECASE),
    re.compile(r"^generated_unit_test(?:_(\d+))?$", re.IGNORECASE),
    re.compile(r"^generated_tests?(?:_(\d+))?$", re.IGNORECASE),
    re.compile(r"^llm_generated_tests?(?:_(\d+))?$", re.IGNORECASE),
)


def find_dataset(explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Dataset file not found: {path}")
        return path

    for candidate in DEFAULT_DATASET_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "Python_Dataset.json was not found. Place it beside this script, "
        "inside the data/ directory, or pass --dataset PATH."
    )


def clean_source(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("_x000D_", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip() + "\n"


def get_generated_test_fields(record: dict[str, Any]) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for key, value in record.items():
        if not isinstance(value, str) or not value.strip():
            continue
        for pattern in GENERATED_FIELD_PATTERNS:
            match = pattern.match(str(key))
            if match:
                repeat = int(match.group(1) or 1)
                found.append((repeat, str(key), value))
                break
    return sorted(found, key=lambda item: item[0])


def extract_test_runs(
    record: dict[str, Any],
    test_source: str,
) -> list[dict[str, Any]]:
    generated = get_generated_test_fields(record)
    reference = clean_source(record.get("original_unit_tests_from_dataset"))

    if test_source == "generated":
        selected = generated
    elif test_source == "reference":
        selected = [(1, "original_unit_tests_from_dataset", reference)] if reference.strip() else []
    elif test_source == "both":
        selected = list(generated)
        if reference.strip():
            selected.append((0, "original_unit_tests_from_dataset", reference))
    else:
        if generated:
            selected = generated
        elif reference.strip():
            selected = [(0, "original_unit_tests_from_dataset", reference)]
        else:
            selected = []

    runs = []
    for repeat, field_name, test_code in selected:
        if not test_code.strip():
            continue
        source_type = "generated" if field_name != "original_unit_tests_from_dataset" else "reference"
        runs.append({
            "repeat": repeat,
            "field_name": field_name,
            "source_type": source_type,
            "test_code": clean_source(test_code),
        })
    return runs


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))
    return text.strip("._") or "unknown"


def parse_pytest_summary(output: str) -> dict[str, int]:
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    patterns = {
        "passed": r"(\d+)\s+passed",
        "failed": r"(\d+)\s+failed",
        "errors": r"(\d+)\s+errors?",
        "skipped": r"(\d+)\s+skipped",
        "xfailed": r"(\d+)\s+xfailed",
    }
    for key, pattern in patterns.items():
        matches = re.findall(pattern, output, flags=re.IGNORECASE)
        if matches:
            summary[key] = int(matches[-1])
    return summary


def run_one(
    record: dict[str, Any],
    run_info: dict[str, Any],
    output_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    difficulty = str(record.get("difficulty", "Unknown"))
    sample_no = record.get("sample_no", "unknown")
    task_id = record.get("task_id", "unknown")
    function_name = str(record.get("function_name", "unknown"))
    repeat = run_info["repeat"]
    source_type = run_info["source_type"]

    run_id = "__".join([
        safe_name(difficulty),
        f"sample_{safe_name(sample_no)}",
        f"task_{safe_name(task_id)}",
        safe_name(source_type),
        f"repeat_{repeat}",
    ])
    run_dir = output_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    source_code = clean_source(record.get("original_code_from_dataset"))
    test_code = run_info["test_code"]

    result: dict[str, Any] = {
        "run_id": run_id,
        "difficulty": difficulty,
        "sample_no": sample_no,
        "task_id": task_id,
        "function_name": function_name,
        "cyclomatic_complexity": record.get("cyclomatic_complexity"),
        "loc": record.get("loc"),
        "test_source": source_type,
        "test_field": run_info["field_name"],
        "repeat": repeat,
        "provider": record.get("provider"),
        "model": record.get("model"),
        "execution_status": "NOT_RUN",
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "line_coverage_pct": None,
        "covered_lines": None,
        "total_statements": None,
        "missing_lines": None,
        "branch_coverage_pct": None,
        "covered_branches": None,
        "total_branches": None,
        "error_message": "",
    }

    if not source_code.strip():
        result["execution_status"] = "MISSING_SOURCE"
        result["error_message"] = "original_code_from_dataset is empty."
        return result

    (run_dir / "solution.py").write_text(source_code, encoding="utf-8")
    test_file = (
        "import solution\n"
        "globals().update({name: value for name, value in vars(solution).items() "
        "if not name.startswith('__')})\n\n"
        + test_code
    )
    (run_dir / "test_solution.py").write_text(test_file, encoding="utf-8")

    coverage_json = run_dir / "coverage.json"
    command = [
        sys.executable, "-m", "coverage", "run",
        "--branch",
        "--source=solution",
        "-m", "pytest",
        "-q",
        "test_solution.py",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(run_dir) + os.pathsep + env.get("PYTHONPATH", "")

    try:
        completed = subprocess.run(
            command,
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        combined_output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        (run_dir / "execution.log").write_text(combined_output, encoding="utf-8")

        counts = parse_pytest_summary(combined_output)
        result.update(counts)

        if "no tests ran" in combined_output.lower():
            result["execution_status"] = "NO_TESTS_COLLECTED"
        elif counts["errors"] > 0:
            result["execution_status"] = "TEST_ERROR"
        elif counts["failed"] > 0:
            result["execution_status"] = "TESTS_FAILED"
        elif completed.returncode == 0 and counts["passed"] > 0:
            result["execution_status"] = "PASSED"
        elif completed.returncode == 0:
            result["execution_status"] = "COMPLETED"
        else:
            result["execution_status"] = "EXECUTION_ERROR"

        cov_json_result = subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", str(coverage_json)],
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if cov_json_result.returncode == 0 and coverage_json.exists():
            coverage_data = json.loads(coverage_json.read_text(encoding="utf-8"))
            file_data = coverage_data.get("files", {}).get("solution.py", {})
            summary = file_data.get("summary", {})
            result["line_coverage_pct"] = summary.get("percent_covered")
            result["covered_lines"] = summary.get("covered_lines")
            result["total_statements"] = summary.get("num_statements")
            result["missing_lines"] = summary.get("missing_lines")
            result["branch_coverage_pct"] = summary.get("percent_covered_branches")
            result["covered_branches"] = summary.get("covered_branches")
            result["total_branches"] = summary.get("num_branches")
        else:
            result["error_message"] = (
                cov_json_result.stderr.strip() or cov_json_result.stdout.strip()
            )
    except subprocess.TimeoutExpired:
        result["execution_status"] = "TIMEOUT"
        result["error_message"] = f"Execution exceeded {timeout_seconds} seconds."
    except Exception as exc:
        result["execution_status"] = "RUNNER_ERROR"
        result["error_message"] = f"{type(exc).__name__}: {exc}"
        (run_dir / "runner_error.txt").write_text(
            traceback.format_exc(), encoding="utf-8"
        )

    return result


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_excel(rows: list[dict[str, Any]], path: Path) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return False

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Coverage Results"
    if rows:
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(key) for key in headers])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column_cells in sheet.columns:
            width = min(max(len(str(cell.value or "")) for cell in column_cells) + 2, 36)
            sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = width

    summary = workbook.create_sheet("Difficulty Summary")
    summary.append([
        "Difficulty", "Runs", "Passed Runs", "Failed Runs",
        "Mean Line Coverage (%)", "Mean Branch Coverage (%)"
    ])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["difficulty"])].append(row)
    for difficulty, group in sorted(grouped.items()):
        line_values = [r["line_coverage_pct"] for r in group if isinstance(r["line_coverage_pct"], (int, float))]
        branch_values = [r["branch_coverage_pct"] for r in group if isinstance(r["branch_coverage_pct"], (int, float))]
        summary.append([
            difficulty,
            len(group),
            sum(r["execution_status"] == "PASSED" for r in group),
            sum(r["execution_status"] in {"TESTS_FAILED", "TEST_ERROR", "EXECUTION_ERROR"} for r in group),
            round(sum(line_values) / len(line_values), 2) if line_values else None,
            round(sum(branch_values) / len(branch_values), 2) if branch_values else None,
        ])
    for cell in summary[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    summary.freeze_panes = "A2"
    workbook.save(path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute Python dataset tests and measure line/branch coverage."
    )
    parser.add_argument("--dataset", help="Path to Python_Dataset.json.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory (default: repository/output).",
    )
    parser.add_argument(
        "--test-source",
        choices=["auto", "generated", "reference", "both"],
        default="auto",
        help=(
            "auto prefers generated tests and falls back to reference tests; "
            "reference runs original dataset tests; both runs both types."
        ),
    )
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per run in seconds.")
    args = parser.parse_args()

    if shutil.which(sys.executable) is None:
        raise RuntimeError("The current Python executable could not be located.")

    for module in ("pytest", "coverage"):
        check = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            print(f"Missing dependency: {module}. Install requirements.txt first.")
            return 2

    dataset_path = find_dataset(args.dataset)
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    levels = dataset.get("difficulty_levels", {})
    records = []
    for level_name, level_data in levels.items():
        for record in level_data.get("records", []):
            record = dict(record)
            record.setdefault("difficulty", level_name.title())
            records.append(record)

    audit_rows = []
    execution_jobs = []
    for record in records:
        generated_fields = get_generated_test_fields(record)
        reference_present = bool(clean_source(record.get("original_unit_tests_from_dataset")).strip())
        jobs = extract_test_runs(record, args.test_source)
        execution_jobs.extend((record, job) for job in jobs)
        audit_rows.append({
            "difficulty": record.get("difficulty"),
            "sample_no": record.get("sample_no"),
            "task_id": record.get("task_id"),
            "function_name": record.get("function_name"),
            "has_source_code": bool(clean_source(record.get("original_code_from_dataset")).strip()),
            "has_reference_tests": reference_present,
            "generated_test_fields": len(generated_fields),
            "selected_test_runs": len(jobs),
        })

    print(f"Dataset: {dataset_path}")
    print(f"Dataset records: {len(records)}")
    print(f"Selected execution runs: {len(execution_jobs)}")
    print(f"Test source mode: {args.test_source}")

    results = []
    for index, (record, job) in enumerate(execution_jobs, start=1):
        label = (
            f"{record.get('difficulty')} | sample {record.get('sample_no')} | "
            f"{record.get('function_name')} | {job['source_type']} | repeat {job['repeat']}"
        )
        print(f"[{index}/{len(execution_jobs)}] {label}")
        results.append(run_one(record, job, output_dir, args.timeout))

    write_csv(results, output_dir / "coverage_results.csv")
    write_csv(audit_rows, output_dir / "dataset_audit.csv")
    (output_dir / "coverage_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    excel_created = write_excel(results, output_dir / "coverage_results.xlsx")
    status_counts: dict[str, int] = defaultdict(int)
    for row in results:
        status_counts[row["execution_status"]] += 1

    summary = {
        "dataset_path": str(dataset_path),
        "dataset_name": dataset.get("dataset_name"),
        "total_dataset_records": len(records),
        "selected_execution_runs": len(execution_jobs),
        "test_source_mode": args.test_source,
        "execution_status_counts": dict(status_counts),
        "excel_created": excel_created,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\nExecution summary")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"Results directory: {output_dir}")
    print(f"Excel report created: {excel_created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
