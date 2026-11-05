"""Reconstructed Pynguin runner for the Python unit-test generation experiment.

Experimental design:
- 30 target functions: 10 Easy, 10 Medium, 10 Hard.
- Five independent Pynguin invocations per function.
- Repetition seeds: 1, 2, 3, 4, 5.
- Search algorithm: DYNAMOSA.
- Protected visibility: includes public and single-underscore functions.
- Pynguin search-time budget: 300 seconds (soft limit).
- External process timeout: 330 seconds.

The exact Pynguin package version used in the historical run was not present
in the preserved experiment artifacts. This script records the installed
version at execution time instead of assuming an unverified version.

Pynguin imports and executes the source module during generation. Only run
this script on source code you trust. The script asks for an explicit
acknowledgement before enabling Pynguin's danger-awareness environment flag.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


# ============================================================================
# EXPERIMENT CONFIGURATION
# ============================================================================

INPUT_DIR = Path(os.environ.get("SQAI_INPUT_DIR", "/content"))
OUTPUT_DIR = Path(os.environ.get("PYNGUIN_OUTPUT_DIR", "/content/pynguin_experiment"))
WORKSPACE_DIR = OUTPUT_DIR / "workspaces"
ARTIFACTS_DIR = OUTPUT_DIR / "generated_test_files"
REPORTS_DIR = OUTPUT_DIR / "pynguin_reports"
LOGS_DIR = OUTPUT_DIR / "process_logs"
CHECKPOINT_PATH = OUTPUT_DIR / "pynguin_checkpoint.json"
EXCEL_PATH = OUTPUT_DIR / "Pynguin_150_Test_Generations.xlsx"
ZIP_PATH = OUTPUT_DIR / "Pynguin_150_Test_Generations_Complete.zip"

REPETITIONS = [1, 2, 3, 4, 5]
ALGORITHM = "DYNAMOSA"
ELEMENT_VISIBILITY = "PROTECTED"
PYNGUIN_SEARCH_TIME_SECONDS = 300
PROCESS_TIMEOUT_SECONDS = 330
STATISTICS_BACKEND = "CSV"

EXPECTED_FUNCTIONS_PER_LEVEL = 10
EXPECTED_LEVELS = ["Easy", "Medium", "Hard"]
EXPECTED_TOTAL_RUNS = 150
EXCEL_CELL_CHAR_LIMIT = 32000

DATASET_FILES = {
    "Easy": "easy_python_dataset.xlsx",
    "Medium": "midum_python_dataset.xlsx",
    "Hard": "Hard_python_dataset.xlsx",
}

CODE_COLUMN_ALIASES = [
    "original_code_from_dataset",
    "original_code",
    "source_code",
    "function_code",
    "code",
]
FUNCTION_COLUMN_ALIASES = ["function_name", "function", "method_name"]
TASK_ID_ALIASES = ["task_id", "task", "id"]
SAMPLE_NO_ALIASES = ["sample_no", "sample_index", "sample", "sample_number"]
DIFFICULTY_ALIASES = ["difficulty", "level"]
CC_ALIASES = ["cyclomatic_complexity", "cc", "complexity"]
LOC_ALIASES = ["loc", "source_loc", "original_source_loc"]

TERMINAL_STATUSES = {
    "GENERATED",
    "GENERATED_WITH_WARNING",
    "GENERATION_TIMEOUT",
    "NO_TEST_GENERATED",
    "GENERATOR_ERROR",
    "INVALID_SOURCE",
    "MISSING_SOURCE_CODE",
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# GENERAL HELPERS
# ============================================================================


def normalize_column_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_file_stem(value: str) -> str:
    stem = Path(value).stem.lower().strip()
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return re.sub(r"[^a-z0-9]+", "", stem)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_name(value: Any) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = text.strip("._")
    return text or "unknown"


def clip_excel_text(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= EXCEL_CELL_CHAR_LIMIT:
        return value
    marker = "\n\n[TRUNCATED IN EXCEL CELL. SEE THE FULL .py FILE USING Full_Suite_Path.]\n"
    return value[: EXCEL_CELL_CHAR_LIMIT - len(marker)] + marker


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(path.suffix + ".corrupt")
        shutil.copy2(path, backup)
        print(f"WARNING: unreadable checkpoint copied to {backup}")
    return default


def first_existing_column(columns: list[Any], aliases: list[str]) -> Any | None:
    lookup = {normalize_column_name(column): column for column in columns}
    for alias in aliases:
        key = normalize_column_name(alias)
        if key in lookup:
            return lookup[key]
    return None


def stringify_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


# ============================================================================
# INPUT FILE DISCOVERY AND UPLOAD
# ============================================================================


def discover_dataset_file(level: str) -> Path | None:
    expected = normalize_file_stem(DATASET_FILES[level])
    excluded = (
        "zeroshot",
        "generated",
        "pynguin",
        "qwen",
        "coverage",
        "evaluation",
        "result",
        "complete",
        "summary",
    )

    candidates: list[Path] = []
    for path in INPUT_DIR.glob("*.xlsx"):
        stem_norm = normalize_file_stem(path.name)
        if any(term in stem_norm for term in excluded):
            continue
        if stem_norm == expected:
            candidates.append(path)

    if not candidates:
        return None

    exact = [p for p in candidates if Path(p.name).stem.lower() == Path(DATASET_FILES[level]).stem.lower()]
    if exact:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]

    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
    print(f"WARNING: multiple copies found for {level}; using {candidates[0].name}")
    return candidates[0]


def ensure_input_files() -> dict[str, Path]:
    selected = {level: discover_dataset_file(level) for level in EXPECTED_LEVELS}
    missing = [level for level, path in selected.items() if path is None]

    if missing:
        print("Upload the three original Python source workbooks:")
        for level in missing:
            print(f" - {DATASET_FILES[level]}")
        try:
            from google.colab import files

            files.upload()
        except ImportError as exc:
            raise FileNotFoundError(
                "Missing input workbooks. Place the original Easy, Medium, and Hard Excel files in INPUT_DIR."
            ) from exc

        selected = {level: discover_dataset_file(level) for level in EXPECTED_LEVELS}
        missing = [level for level, path in selected.items() if path is None]

    if missing:
        raise FileNotFoundError(
            "Could not identify these required files: "
            + ", ".join(DATASET_FILES[level] for level in missing)
        )

    print("Input workbooks:")
    for level in EXPECTED_LEVELS:
        print(f"  {level}: {selected[level]}")

    return {level: path for level, path in selected.items() if path is not None}


# ============================================================================
# DATASET STANDARDISATION
# ============================================================================


def load_level_dataset(level: str, path: Path) -> list[dict[str, Any]]:
    workbook = pd.ExcelFile(path)
    frames: list[pd.DataFrame] = []

    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet_name)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["_source_sheet"] = sheet_name
        frames.append(frame)

    if not frames:
        raise ValueError(f"No non-empty worksheet found in {path.name}")

    data = pd.concat(frames, ignore_index=True, sort=False)
    code_col = first_existing_column(list(data.columns), CODE_COLUMN_ALIASES)
    function_col = first_existing_column(list(data.columns), FUNCTION_COLUMN_ALIASES)
    task_col = first_existing_column(list(data.columns), TASK_ID_ALIASES)
    sample_col = first_existing_column(list(data.columns), SAMPLE_NO_ALIASES)
    difficulty_col = first_existing_column(list(data.columns), DIFFICULTY_ALIASES)
    cc_col = first_existing_column(list(data.columns), CC_ALIASES)
    loc_col = first_existing_column(list(data.columns), LOC_ALIASES)

    if code_col is None:
        raise ValueError(
            f"Could not locate the original source-code column in {path.name}. "
            f"Expected one of {CODE_COLUMN_ALIASES}; found {list(data.columns)}"
        )
    if function_col is None:
        raise ValueError(
            f"Could not locate the function-name column in {path.name}. "
            f"Expected one of {FUNCTION_COLUMN_ALIASES}; found {list(data.columns)}"
        )

    records: list[dict[str, Any]] = []
    for source_row, row in data.iterrows():
        source_code = stringify_cell(row[code_col])
        function_name = stringify_cell(row[function_col])
        if not function_name and source_code:
            try:
                tree = ast.parse(source_code)
                function_nodes = [
                    node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                function_name = function_nodes[0].name if function_nodes else f"row_{source_row + 1}"
            except SyntaxError:
                function_name = f"row_{source_row + 1}"

        records.append(
            {
                "difficulty": stringify_cell(row[difficulty_col]) if difficulty_col is not None else level,
                "sample_no": row[sample_col] if sample_col is not None else source_row + 1,
                "task_id": row[task_col] if task_col is not None else None,
                "function_name": function_name or f"row_{source_row + 1}",
                "cyclomatic_complexity": row[cc_col] if cc_col is not None else None,
                "source_loc": row[loc_col] if loc_col is not None else None,
                "original_code": source_code,
                "source_excel_file": path.name,
                "source_sheet": stringify_cell(row.get("_source_sheet", "")),
                "source_row": int(source_row),
            }
        )

    records.sort(
        key=lambda item: (
            str(item["function_name"]).lower(),
            str(item["task_id"]),
            int(item["source_row"]),
        )
    )

    if len(records) != EXPECTED_FUNCTIONS_PER_LEVEL:
        print(
            f"WARNING: {level} contains {len(records)} non-empty records; "
            f"the historical design expected {EXPECTED_FUNCTIONS_PER_LEVEL}."
        )

    return records


# ============================================================================
# PYNGUIN CLI DISCOVERY
# ============================================================================


def find_pynguin_executable() -> list[str]:
    executable = shutil.which("pynguin")
    if executable:
        return [executable]
    return [sys.executable, "-m", "pynguin"]


def get_pynguin_version(executable: list[str]) -> str:
    try:
        completed = subprocess.run(
            executable + ["--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        text = (completed.stdout or completed.stderr).strip()
        return text or f"version command returned {completed.returncode}"
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def get_cli_help(executable: list[str]) -> str:
    completed = subprocess.run(
        executable + ["--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    help_text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if not help_text.strip():
        raise RuntimeError("Could not read Pynguin CLI help. Check the installation.")
    return help_text


def select_cli_option(help_text: str, aliases: list[str], required: bool = True) -> str | None:
    for option in aliases:
        if option in help_text:
            return option
    if required:
        raise RuntimeError(f"Installed Pynguin CLI does not expose any of these options: {aliases}")
    return None


def build_pynguin_command(
    executable: list[str],
    help_text: str,
    project_dir: Path,
    output_dir: Path,
    report_dir: Path,
    log_file: Path,
    seed: int,
    level: str,
    function_name: str,
    task_id: Any,
) -> list[str]:
    options = {
        "project_path": select_cli_option(help_text, ["--project-path", "--project_path"]),
        "module_name": select_cli_option(help_text, ["--module-name", "--module_name"]),
        "output_path": select_cli_option(help_text, ["--output-path", "--output_path"]),
        "algorithm": select_cli_option(help_text, ["--algorithm"]),
        "seed": select_cli_option(help_text, ["--seed"]),
        "search_time": select_cli_option(help_text, ["--maximum-search-time", "--maximum_search_time"]),
        "visibility": select_cli_option(help_text, ["--element-visibility", "--element_visibility"]),
        "report_dir": select_cli_option(help_text, ["--report-dir", "--report_dir"], required=False),
        "statistics_backend": select_cli_option(help_text, ["--statistics-backend", "--statistics_backend"], required=False),
        "configuration_id": select_cli_option(help_text, ["--configuration-id", "--configuration_id"], required=False),
        "project_name": select_cli_option(help_text, ["--project-name", "--project_name"], required=False),
        "log_file": select_cli_option(help_text, ["--log-file", "--log_file"], required=False),
    }

    command = executable.copy()
    command += [options["project_path"], str(project_dir)]
    command += [options["module_name"], "sut"]
    command += [options["output_path"], str(output_dir)]
    command += [options["algorithm"], ALGORITHM]
    command += [options["seed"], str(seed)]
    command += [options["search_time"], str(PYNGUIN_SEARCH_TIME_SECONDS)]
    command += [options["visibility"], ELEMENT_VISIBILITY]

    if options["report_dir"]:
        command += [options["report_dir"], str(report_dir)]
    if options["statistics_backend"]:
        command += [options["statistics_backend"], STATISTICS_BACKEND]
    if options["configuration_id"]:
        command += [options["configuration_id"], f"{level}_task_{task_id}_rep_{seed}"]
    if options["project_name"]:
        command += [options["project_name"], str(function_name)]
    if options["log_file"]:
        command += [options["log_file"], str(log_file)]

    return command


# ============================================================================
# GENERATION AND RESULT EXTRACTION
# ============================================================================


def find_generated_test_files(output_dir: Path) -> list[Path]:
    files = sorted(
        path for path in output_dir.rglob("*.py")
        if path.name not in {"sut.py", "__init__.py"}
    )
    preferred = [path for path in files if path.name.startswith("test_")]
    return preferred or files


def count_test_functions(test_code: str) -> int:
    return len(
        re.findall(
            r"(?m)^\s*def\s+test_[A-Za-z0-9_]*\s*\(",
            test_code,
        )
    )


def read_generated_suites(test_files: list[Path]) -> str:
    parts: list[str] = []
    for path in test_files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            content = f"# Could not read {path.name}: {exc}"
        if len(test_files) > 1:
            parts.append(f"# --- BEGIN {path.name} ---\n{content}\n# --- END {path.name} ---")
        else:
            parts.append(content)
    return "\n\n".join(parts).strip()


def save_log(path: Path, text: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8", errors="replace")


def execute_one_repetition(
    executable: list[str],
    help_text: str,
    level: str,
    record: dict[str, Any],
    repetition: int,
) -> dict[str, Any]:
    function_name = str(record["function_name"])
    task_id = record.get("task_id")
    task_label = safe_name(task_id if task_id is not None else record.get("sample_no"))
    function_label = safe_name(function_name)
    source_hash = sha256_text(record["original_code"])

    run_key = f"{level}|row_{record['source_row']}|task_{task_label}|rep_{repetition}"
    run_root = WORKSPACE_DIR / level / f"{function_label}_{task_label}" / f"rep_{repetition}"
    project_dir = run_root / "project"
    test_output_dir = run_root / "generated_tests"
    report_dir = REPORTS_DIR / level / f"{function_label}_{task_label}" / f"rep_{repetition}"
    log_dir = LOGS_DIR / level / f"{function_label}_{task_label}"
    stdout_path = log_dir / f"rep_{repetition}.stdout.txt"
    stderr_path = log_dir / f"rep_{repetition}.stderr.txt"
    pynguin_log_path = log_dir / f"rep_{repetition}.pynguin.log"

    project_dir.mkdir(parents=True, exist_ok=True)
    test_output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    source_code = record["original_code"]
    if not source_code.strip():
        return {
            "run_key": run_key,
            "level": level,
            "task_id": task_id,
            "sample_no": record.get("sample_no"),
            "function_name": function_name,
            "repetition": repetition,
            "seed": repetition,
            "status": "MISSING_SOURCE_CODE",
            "suite_present": False,
            "test_count": 0,
            "runtime_seconds": 0.0,
            "return_code": None,
            "source_sha256": source_hash,
            "suite_code": "",
            "suite_file_paths": [],
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "error": "No source code was supplied in the input workbook.",
        }

    try:
        compile(source_code, "sut.py", "exec")
    except SyntaxError as exc:
        return {
            "run_key": run_key,
            "level": level,
            "task_id": task_id,
            "sample_no": record.get("sample_no"),
            "function_name": function_name,
            "repetition": repetition,
            "seed": repetition,
            "status": "INVALID_SOURCE",
            "suite_present": False,
            "test_count": 0,
            "runtime_seconds": 0.0,
            "return_code": None,
            "source_sha256": source_hash,
            "suite_code": "",
            "suite_file_paths": [],
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
        }

    (project_dir / "sut.py").write_text(source_code, encoding="utf-8")

    command = build_pynguin_command(
        executable=executable,
        help_text=help_text,
        project_dir=project_dir,
        output_dir=test_output_dir,
        report_dir=report_dir,
        log_file=pynguin_log_path,
        seed=repetition,
        level=level,
        function_name=function_name,
        task_id=task_id,
    )

    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")
    env["PYNGUIN_DANGER_AWARE"] = "1"

    started = time.time()
    stdout_text = ""
    stderr_text = ""
    return_code: int | None = None
    timeout_recorded = False
    error_text = ""

    try:
        completed = subprocess.run(
            command,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        return_code = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        timeout_recorded = True
        stdout_value = exc.stdout or ""
        stderr_value = exc.stderr or ""
        stdout_text = stdout_value.decode("utf-8", errors="replace") if isinstance(stdout_value, bytes) else str(stdout_value)
        stderr_text = stderr_value.decode("utf-8", errors="replace") if isinstance(stderr_value, bytes) else str(stderr_value)
        error_text = f"PYNGUIN_PROCESS_TIMEOUT: process exceeded {PROCESS_TIMEOUT_SECONDS} seconds."
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"

    elapsed = round(time.time() - started, 3)
    save_log(stdout_path, stdout_text)
    save_log(stderr_path, stderr_text)

    generated_files = find_generated_test_files(test_output_dir)
    suite_code = read_generated_suites(generated_files) if generated_files else ""

    artifact_paths: list[str] = []
    for file_index, generated_file in enumerate(generated_files, start=1):
        artifact_dir = ARTIFACTS_DIR / level / f"{function_label}_{task_label}" / f"rep_{repetition}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        destination_name = generated_file.name if len(generated_files) == 1 else f"suite_{file_index}_{generated_file.name}"
        destination = artifact_dir / destination_name
        shutil.copy2(generated_file, destination)
        artifact_paths.append(str(destination.relative_to(OUTPUT_DIR)))

    if timeout_recorded:
        status = "GENERATION_TIMEOUT"
    elif generated_files and return_code == 0:
        status = "GENERATED"
    elif generated_files and return_code not in (None, 0):
        status = "GENERATED_WITH_WARNING"
    elif return_code == 0:
        status = "NO_TEST_GENERATED"
    else:
        status = "GENERATOR_ERROR"
        if not error_text:
            error_text = stderr_text[-8000:] or stdout_text[-8000:] or f"Pynguin exited with return code {return_code}."

    return {
        "run_key": run_key,
        "level": level,
        "task_id": task_id,
        "sample_no": record.get("sample_no"),
        "function_name": function_name,
        "repetition": repetition,
        "seed": repetition,
        "status": status,
        "suite_present": bool(generated_files),
        "test_count": count_test_functions(suite_code),
        "runtime_seconds": elapsed,
        "return_code": return_code,
        "timeout_recorded": timeout_recorded,
        "source_sha256": source_hash,
        "suite_code": suite_code,
        "suite_file_paths": artifact_paths,
        "stdout_path": str(stdout_path.relative_to(OUTPUT_DIR)),
        "stderr_path": str(stderr_path.relative_to(OUTPUT_DIR)),
        "error": error_text,
        "command": command,
    }


# ============================================================================
# CHECKPOINT AND WORKBOOK EXPORT
# ============================================================================


def make_run_key(level: str, record: dict[str, Any], repetition: int) -> str:
    task_id = record.get("task_id")
    task_label = safe_name(task_id if task_id is not None else record.get("sample_no"))
    return f"{level}|row_{record['source_row']}|task_{task_label}|rep_{repetition}"


def save_excel_atomic(
    all_records: dict[str, list[dict[str, Any]]],
    checkpoint: dict[str, Any],
    pynguin_version: str,
    input_files: dict[str, Path],
) -> None:
    run_map = checkpoint.get("runs", {})
    run_rows: list[dict[str, Any]] = []
    wide_rows: list[dict[str, Any]] = []
    function_rows: list[dict[str, Any]] = []

    for level in EXPECTED_LEVELS:
        for record in all_records.get(level, []):
            base = {
                "Difficulty": level,
                "Project Name": record["function_name"],
                "Sample No": record.get("sample_no"),
                "Task ID": record.get("task_id"),
                "Function Name": record["function_name"],
                "Cyclomatic Complexity": record.get("cyclomatic_complexity"),
                "Source LOC": record.get("source_loc"),
            }
            wide = dict(base)
            per_function_runs: list[dict[str, Any]] = []

            for repetition in REPETITIONS:
                key = make_run_key(level, record, repetition)
                result = run_map.get(key, {})
                suite_code = result.get("suite_code", "")
                wide[f"Repetition_{repetition}"] = clip_excel_text(suite_code)
                per_function_runs.append(result)

                run_rows.append(
                    {
                        **base,
                        "Repetition": repetition,
                        "Seed": repetition,
                        "Status": result.get("status", "NOT_STARTED"),
                        "Suite Present": bool(result.get("suite_present", False)),
                        "Generated Test Functions": result.get("test_count", 0),
                        "Runtime (s)": result.get("runtime_seconds"),
                        "Pynguin Return Code": result.get("return_code"),
                        "Timeout Recorded": bool(result.get("timeout_recorded", False)),
                        "Full Suite Path(s)": "; ".join(result.get("suite_file_paths", [])),
                        "Stdout Path": result.get("stdout_path"),
                        "Stderr Path": result.get("stderr_path"),
                        "Error": result.get("error", ""),
                        "Source SHA256": result.get("source_sha256", sha256_text(record["original_code"])),
                        "Generated Suite": clip_excel_text(suite_code),
                    }
                )

            wide_rows.append(wide)
            statuses = [r.get("status", "NOT_STARTED") for r in per_function_runs]
            function_rows.append(
                {
                    **base,
                    "Expected Repetitions": len(REPETITIONS),
                    "Suites Present": sum(bool(r.get("suite_present", False)) for r in per_function_runs),
                    "Generation Timeouts": statuses.count("GENERATION_TIMEOUT"),
                    "No Test Generated": statuses.count("NO_TEST_GENERATED"),
                    "Generator Errors": statuses.count("GENERATOR_ERROR"),
                    "Invalid Source Runs": statuses.count("INVALID_SOURCE"),
                    "Generated With Warning": statuses.count("GENERATED_WITH_WARNING"),
                    "Runs Not Started": statuses.count("NOT_STARTED"),
                    "Generated Test Functions Total": sum(int(r.get("test_count", 0) or 0) for r in per_function_runs),
                }
            )

    run_df = pd.DataFrame(run_rows)
    generated_df = pd.DataFrame(wide_rows)
    function_df = pd.DataFrame(function_rows)

    if not run_df.empty:
        level_summary = (
            run_df.groupby("Difficulty", dropna=False)
            .agg(
                Functions=("Function Name", "nunique"),
                Expected_Repetitions=("Repetition", "count"),
                Suites_Present=("Suite Present", "sum"),
                Timeouts=("Timeout Recorded", "sum"),
                Generated_Test_Functions=("Generated Test Functions", "sum"),
            )
            .reset_index()
        )
        level_summary = level_summary.rename(
            columns={
                "Expected_Repetitions": "Expected Repetitions",
                "Suites_Present": "Suites Present",
                "Generated_Test_Functions": "Generated Test Functions",
            }
        )
    else:
        level_summary = pd.DataFrame()

    completed_count = sum(
        result.get("status") in TERMINAL_STATUSES
        for result in run_map.values()
    )
    successful_count = sum(
        result.get("status") in {"GENERATED", "GENERATED_WITH_WARNING"}
        for result in run_map.values()
    )
    timeout_count = sum(result.get("status") == "GENERATION_TIMEOUT" for result in run_map.values())
    no_test_count = sum(result.get("status") == "NO_TEST_GENERATED" for result in run_map.values())
    error_count = sum(result.get("status") in {"GENERATOR_ERROR", "INVALID_SOURCE", "MISSING_SOURCE_CODE"} for result in run_map.values())

    config_rows = [
        {"Setting": "Pynguin Version", "Value": pynguin_version},
        {"Setting": "Python Version", "Value": sys.version.replace("\n", " ")},
        {"Setting": "Algorithm", "Value": ALGORITHM},
        {"Setting": "Element Visibility", "Value": ELEMENT_VISIBILITY},
        {"Setting": "Repetition Seeds", "Value": ", ".join(map(str, REPETITIONS))},
        {"Setting": "Expected Functions per Difficulty", "Value": EXPECTED_FUNCTIONS_PER_LEVEL},
        {"Setting": "Expected Total Functions", "Value": EXPECTED_FUNCTIONS_PER_LEVEL * len(EXPECTED_LEVELS)},
        {"Setting": "Expected Total Runs", "Value": EXPECTED_TOTAL_RUNS},
        {"Setting": "Maximum Search Time (seconds)", "Value": PYNGUIN_SEARCH_TIME_SECONDS},
        {"Setting": "External Process Timeout (seconds)", "Value": PROCESS_TIMEOUT_SECONDS},
        {"Setting": "Statistics Backend", "Value": STATISTICS_BACKEND},
        {"Setting": "Output Directory", "Value": str(OUTPUT_DIR)},
        {"Setting": "Checkpoint", "Value": str(CHECKPOINT_PATH)},
    ]
    for level, path in input_files.items():
        config_rows.append({"Setting": f"Input File - {level}", "Value": path.name})

    methodology_rows = [
        {"Topic": "Experimental design", "Description": "10 functions per difficulty level (Easy, Medium, Hard), with five independent Pynguin invocations per function; 150 expected runs."},
        {"Topic": "Repetitions", "Description": "Each repetition is a separate Pynguin process invocation with seed 1, 2, 3, 4, or 5. Previous generated suites are not passed into later repetitions."},
        {"Topic": "Target module", "Description": "Each focal code snippet is written unchanged to an isolated module named sut.py; Pynguin is invoked with module name sut."},
        {"Topic": "Algorithm", "Description": "DYNAMOSA, explicitly set to match the algorithm listed as the default in the preserved Pynguin CLI help."},
        {"Topic": "Element visibility", "Description": "PROTECTED includes public and single-underscore functions. This is necessary for focal names such as _checkIPValidity and _is_unit in the supplied dataset."},
        {"Topic": "Search budget", "Description": "The Pynguin internal maximum-search-time is set to 300 seconds. Pynguin documents this stopping condition as a soft limit checked at algorithm-iteration boundaries."},
        {"Topic": "External timeout", "Description": "The parent process enforces a 330-second hard timeout, matching the timeout duration recorded in the preserved experiment logs."},
        {"Topic": "Prompt", "Description": "No natural-language prompt is used. Pynguin is a search-based generator that receives the source module and command-line configuration."},
        {"Topic": "Execution evaluation", "Description": "This workbook captures generation artifacts and process outcomes. Pytest execution, coverage, and quality metrics are separate evaluation stages and are not fabricated here."},
        {"Topic": "Pynguin version", "Description": "The archived experiment materials did not identify the exact installed package version. The version detected at runtime is recorded in the Configuration sheet."},
    ]

    validation_rows = [
        {"Check": "Expected run count", "Result": "PASS" if EXPECTED_TOTAL_RUNS == 150 else "CHECK", "Value": EXPECTED_TOTAL_RUNS, "Notes": "30 functions multiplied by five repetitions."},
        {"Check": "Recorded runs", "Result": "PASS" if completed_count == EXPECTED_TOTAL_RUNS else "INCOMPLETE", "Value": completed_count, "Notes": "Counts terminal statuses in the checkpoint."},
        {"Check": "Generated suites", "Result": "INFO", "Value": successful_count, "Notes": "Includes GENERATED and GENERATED_WITH_WARNING statuses."},
        {"Check": "Generation timeouts", "Result": "INFO", "Value": timeout_count, "Notes": "External process exceeded the configured timeout."},
        {"Check": "No test generated", "Result": "INFO", "Value": no_test_count, "Notes": "Pynguin completed without an exported test file."},
        {"Check": "Other generation errors", "Result": "INFO", "Value": error_count, "Notes": "Includes generator errors, invalid source, and missing source code."},
    ]

    temp_excel = EXCEL_PATH.with_suffix(".tmp.xlsx")
    with pd.ExcelWriter(temp_excel, engine="openpyxl") as writer:
        generated_df.to_excel(writer, sheet_name="Generated_Tests", index=False)
        run_df.to_excel(writer, sheet_name="Run_Details", index=False)
        function_df.to_excel(writer, sheet_name="Function_Summary", index=False)
        level_summary.to_excel(writer, sheet_name="Difficulty_Summary", index=False)
        pd.DataFrame(config_rows).to_excel(writer, sheet_name="Configuration", index=False)
        pd.DataFrame(methodology_rows).to_excel(writer, sheet_name="Methodology", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="Validation", index=False)

    os.replace(temp_excel, EXCEL_PATH)


# ============================================================================
# MAIN EXPERIMENT
# ============================================================================


def main() -> None:
    print("=" * 78)
    print("PYNGUIN PYTHON TEST-GENERATION EXPERIMENT")
    print("=" * 78)
    print("The generator imports and executes the supplied source code.")
    print("Only continue if the uploaded source files are trusted.")
    acknowledgement = input('Type TRUSTED to confirm and continue: ').strip()
    if acknowledgement != "TRUSTED":
        raise SystemExit("Execution cancelled: trust acknowledgement was not provided.")

    os.environ["PYNGUIN_DANGER_AWARE"] = "1"

    input_files = ensure_input_files()
    all_records = {
        level: load_level_dataset(level, input_files[level])
        for level in EXPECTED_LEVELS
    }

    total_functions = sum(len(records) for records in all_records.values())
    expected_runs = total_functions * len(REPETITIONS)
    print(f"Functions loaded: {total_functions}")
    print(f"Expected generation runs: {expected_runs}")

    executable = find_pynguin_executable()
    pynguin_version = get_pynguin_version(executable)
    help_text = get_cli_help(executable)

    required_flags = [
        ["--project-path", "--project_path"],
        ["--module-name", "--module_name"],
        ["--output-path", "--output_path"],
        ["--algorithm"],
        ["--seed"],
        ["--maximum-search-time", "--maximum_search_time"],
        ["--element-visibility", "--element_visibility"],
    ]
    for aliases in required_flags:
        select_cli_option(help_text, aliases)

    print(f"Pynguin executable: {' '.join(executable)}")
    print(f"Pynguin version: {pynguin_version}")
    print(f"Algorithm: {ALGORITHM}")
    print(f"Visibility: {ELEMENT_VISIBILITY}")
    print(f"Repetition seeds: {REPETITIONS}")
    print(f"Search budget: {PYNGUIN_SEARCH_TIME_SECONDS}s")
    print(f"External timeout: {PROCESS_TIMEOUT_SECONDS}s")

    checkpoint = read_json(
        CHECKPOINT_PATH,
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "configuration": {
                "algorithm": ALGORITHM,
                "element_visibility": ELEMENT_VISIBILITY,
                "repetitions": REPETITIONS,
                "search_time_seconds": PYNGUIN_SEARCH_TIME_SECONDS,
                "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
                "pynguin_version": pynguin_version,
            },
            "runs": {},
        },
    )
    checkpoint.setdefault("runs", {})
    checkpoint.setdefault("configuration", {})
    checkpoint["configuration"].update(
        {
            "algorithm": ALGORITHM,
            "element_visibility": ELEMENT_VISIBILITY,
            "repetitions": REPETITIONS,
            "search_time_seconds": PYNGUIN_SEARCH_TIME_SECONDS,
            "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
            "pynguin_version": pynguin_version,
        }
    )

    save_excel_atomic(all_records, checkpoint, pynguin_version, input_files)

    completed_this_session = 0
    for level in EXPECTED_LEVELS:
        records = all_records[level]
        print("\n" + "=" * 78)
        print(f"STARTING {level.upper()} ({len(records)} functions)")
        print("=" * 78)

        for record_index, record in enumerate(records, start=1):
            for repetition in REPETITIONS:
                run_key = make_run_key(level, record, repetition)
                existing = checkpoint["runs"].get(run_key)
                expected_source_hash = sha256_text(record["original_code"])

                if (
                    existing
                    and existing.get("source_sha256") == expected_source_hash
                    and existing.get("status") in TERMINAL_STATUSES
                ):
                    print(
                        f"[{record_index}/{len(records)}] {record['function_name']} "
                        f"rep={repetition}: already recorded ({existing.get('status')}); skipping."
                    )
                    continue

                print(
                    f"[{record_index}/{len(records)}] {record['function_name']} "
                    f"rep={repetition}: generating...",
                    flush=True,
                )

                result = execute_one_repetition(
                    executable=executable,
                    help_text=help_text,
                    level=level,
                    record=record,
                    repetition=repetition,
                )
                checkpoint["runs"][run_key] = result
                checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
                write_json_atomic(CHECKPOINT_PATH, checkpoint)
                save_excel_atomic(all_records, checkpoint, pynguin_version, input_files)
                completed_this_session += 1

                print(
                    f"  status={result['status']} | "
                    f"tests={result.get('test_count', 0)} | "
                    f"runtime={result.get('runtime_seconds', 0)}s | "
                    f"saved={EXCEL_PATH.name}",
                    flush=True,
                )

    save_excel_atomic(all_records, checkpoint, pynguin_version, input_files)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        if EXCEL_PATH.exists():
            archive.write(EXCEL_PATH, arcname=EXCEL_PATH.name)
        if CHECKPOINT_PATH.exists():
            archive.write(CHECKPOINT_PATH, arcname=CHECKPOINT_PATH.name)
        for path in ARTIFACTS_DIR.rglob("*.py"):
            archive.write(path, arcname=str(path.relative_to(OUTPUT_DIR)))
        for path in LOGS_DIR.rglob("*.txt"):
            archive.write(path, arcname=str(path.relative_to(OUTPUT_DIR)))
        for path in REPORTS_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(OUTPUT_DIR)))

    all_statuses = [r.get("status") for r in checkpoint["runs"].values()]
    print("\n" + "=" * 78)
    print("PYNGUIN EXPERIMENT SUMMARY")
    print("=" * 78)
    print(f"Expected runs: {expected_runs}")
    print(f"Recorded runs: {len(all_statuses)}")
    for status in sorted(set(all_statuses)):
        print(f"{status}: {all_statuses.count(status)}")
    print(f"Excel: {EXCEL_PATH}")
    print(f"ZIP:   {ZIP_PATH}")

    try:
        from google.colab import files

        files.download(str(ZIP_PATH))
    except ImportError:
        print("Not running in Google Colab. Download the files from OUTPUT_DIR manually.")


if __name__ == "__main__":
    main()
