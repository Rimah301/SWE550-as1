"""Model-agnostic runner for repeated Python and Java unit-test generation.

Each source function or method is submitted in five separate, independent
inference requests. Each request uses a fresh prompt and does not receive the
conversation history or output of any previous repetition.

The prompt asks for five unit tests per request. Thus, each source item has
five independently generated test sets, with each set preserved separately.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd


# ============================================================================
# CONFIGURATION
# ============================================================================

INPUT_DIR = Path(os.environ.get("SQAI_INPUT_DIR", "/content"))
OUTPUT_DIR = Path(os.environ.get("SQAI_OUTPUT_DIR", "/content/generated_results"))
CHECKPOINT_DIR = Path(os.environ.get("SQAI_CHECKPOINT_DIR", "/content/experiment_checkpoints"))

REPETITIONS = 5
SAVE_AFTER_EACH_REPETITION = True

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# INDEPENDENT ZERO-SHOT PROMPT TEMPLATES
# ============================================================================

PYTHON_PROMPT = """This is one independent test-generation run. Generate exactly 5 independent unit tests for the given Python function using pytest.

Requirements:
- Use pytest only.
- Test the function directly; do not rewrite or modify the original implementation.
- Do not use unittest, unittest.mock, hypothesis, or any other testing framework.
- Do not rely on external files, network access, databases, environment-specific state, or randomness.
- Infer the function's intended behavior solely from the provided original code and its interface.
- Cover normal/representative behavior, boundary conditions, invalid or unusual inputs when applicable, and important branch behavior.
- Prefer tests that would detect realistic implementation mistakes rather than merely checking that the function is callable.
- Each test must contain a clear assertion about the expected behavior.
- Keep the tests independent and deterministic.
- Do not use knowledge of hidden/reference implementations.
- Do not refer to, continue, or reuse any previous generation. Treat this request as completely standalone.
- Return only executable pytest test code, with no explanation or markdown fences.

Original Python code:
{original_code}
"""

JAVA_PROMPT = """This is one independent test-generation run. Generate exactly 5 independent unit tests for the given Java method using JUnit.

Requirements:
- Use JUnit only.
- Test the method directly; do not rewrite or modify the original implementation.
- Do not use TestNG, Mockito, PowerMock, JMock, AssertJ, or any other testing framework.
- Do not rely on external files, network access, databases, environment-specific state, or randomness.
- Infer the method's intended behavior solely from the provided original Java code and its interface.
- Cover normal/representative behavior, boundary conditions, invalid or unusual inputs when applicable, and important branch behavior.
- Prefer tests that would detect realistic implementation mistakes rather than merely checking that the function is callable.
- Each test must contain a clear assertion about the expected behavior.
- Keep the tests independent and deterministic.
- Do not use knowledge of hidden/reference implementations.
- Use appropriate standard JUnit assertions.
- Include all necessary JUnit imports.
- Do not refer to, continue, or reuse any previous generation. Treat this request as completely standalone.
- Return exactly 5 independent test methods.
- Return only executable Java JUnit test code, with no explanation or markdown fences.

Original Java code:
{original_code}
"""


# ============================================================================
# DATASET DEFINITIONS
# ============================================================================

DATASET_SPECS = [
    {
        "language": "python",
        "level": "easy",
        "name_clues": ["easy_python_dataset"],
        "code_columns": [
            "original_code", "Original Code", "function_code",
            "Function Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": PYTHON_PROMPT,
    },
    {
        "language": "python",
        "level": "medium",
        "name_clues": ["midum_python_dataset", "medium_python_dataset"],
        "code_columns": [
            "original_code", "Original Code", "function_code",
            "Function Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": PYTHON_PROMPT,
    },
    {
        "language": "python",
        "level": "hard",
        "name_clues": ["hard_python_dataset"],
        "code_columns": [
            "original_code", "Original Code", "function_code",
            "Function Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": PYTHON_PROMPT,
    },
    {
        "language": "java",
        "level": "easy",
        "name_clues": ["java_easy_dataset_cleaned", "java_easy_dataset"],
        "code_columns": [
            "buggy_code_from_dataset", "original_code", "Original Code",
            "java_code", "Java Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": JAVA_PROMPT,
    },
    {
        "language": "java",
        "level": "medium",
        "name_clues": ["java_midum_dataset", "java_medium_dataset"],
        "code_columns": [
            "buggy_code_from_dataset", "original_code", "Original Code",
            "java_code", "Java Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": JAVA_PROMPT,
    },
    {
        "language": "java",
        "level": "hard",
        "name_clues": ["java_hard_dataset_cleaned", "java_hard_dataset"],
        "code_columns": [
            "buggy_code_from_dataset", "original_code", "Original Code",
            "java_code", "Java Code", "source_code", "Source Code", "code", "Code",
        ],
        "prompt": JAVA_PROMPT,
    },
]


# ============================================================================
# INFERENCE ADAPTER CONTRACT
# ============================================================================

def validate_model_call(model_call: Callable[[str], str]) -> None:
    """Validate that a callable inference adapter has been provided."""
    if not callable(model_call):
        raise TypeError("model_call must be a callable that accepts one prompt string.")


def call_model_once(model_call: Callable[[str], str], prompt: str) -> str:
    """Submit exactly one fresh request and return its generated text.

    The adapter must create a new provider request for every invocation. It
    must not append messages to a shared conversation or include prior
    repetitions' prompts or responses.
    """
    response = model_call(prompt)
    if response is None:
        return ""
    return str(response).strip()


# ============================================================================
# FILE AND COLUMN DISCOVERY
# ============================================================================

def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def find_input_file(spec: dict[str, Any]) -> Path:
    """Find the uploaded source workbook for one language and difficulty."""
    excluded_terms = (
        "generated", "results", "output", "repetition", "combined",
        "checkpoint", "zero_shot", "independent_runs",
    )

    candidates = [
        path for path in INPUT_DIR.glob("*.xlsx")
        if not any(term in path.name.lower() for term in excluded_terms)
        and not path.name.lower().endswith(".tmp.xlsx")
    ]

    clues = {normalize_name(item) for item in spec["name_clues"]}

    def base_name(path: Path) -> str:
        stem = re.sub(r"\s*\(\d+\)$", "", path.stem)
        return normalize_name(stem)

    matches = [path for path in candidates if base_name(path) in clues]

    if not matches:
        raise FileNotFoundError(
            f"No input workbook found for {spec['language']} {spec['level']}. "
            f"Expected filename clues: {spec['name_clues']}"
        )

    matches.sort(key=lambda path: ("(" in path.stem, len(path.name), path.name.lower()))
    return matches[0]


def find_code_column(dataframe: pd.DataFrame, spec: dict[str, Any]) -> str | None:
    """Find the source-code column without selecting a fixed/reference version."""
    for column in spec["code_columns"]:
        if column in dataframe.columns:
            return column

    for column in dataframe.columns:
        normalized = str(column).lower()
        if "code" in normalized and not any(
            marker in normalized for marker in ("fixed", "reference", "test")
        ):
            return column

    return None


def make_prompt(template: str, original_code: str) -> str:
    """Insert source code into the prompt template."""
    return template.replace("{original_code}", str(original_code))


# ============================================================================
# CHECKPOINT AND EXCEL EXPORT
# ============================================================================

def checkpoint_path(spec: dict[str, Any]) -> Path:
    return CHECKPOINT_DIR / (
        f"{spec['language']}_{spec['level']}_{REPETITIONS}_independent_runs.jsonl"
    )


def output_path(spec: dict[str, Any]) -> Path:
    return OUTPUT_DIR / (
        f"{spec['language'].title()}_{spec['level'].title()}_"
        f"{REPETITIONS}_Independent_Repetitions.xlsx"
    )


def read_checkpoint(path: Path) -> list[dict[str, Any]]:
    """Read completed run records; ignore an incomplete final JSONL line."""
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file_handle:
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    """Append one completed repetition and flush it to disk immediately."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        file_handle.flush()


def safe_cell_value(value: Any) -> Any:
    """Convert pandas missing values to Excel-friendly blanks."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def records_to_frames(records: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create long-form run records and a wide summary with one row per source item."""
    if not records:
        return pd.DataFrame(), pd.DataFrame()

    runs_df = pd.DataFrame(records)
    identity_columns = [
        "language", "difficulty", "source_file", "source_sheet",
        "source_row", "source_index",
    ]
    excluded_from_source = {
        "repetition", "prompt", "response", "status", "error", "timestamp"
    }
    source_columns = [
        column for column in runs_df.columns
        if column not in excluded_from_source and column not in identity_columns
    ]

    key_columns = [
        "language", "difficulty", "source_file", "source_sheet",
        "source_row", "source_index",
    ]

    summary_rows: list[dict[str, Any]] = []
    for key, group in runs_df.groupby(key_columns, dropna=False, sort=False):
        if not isinstance(key, tuple):
            key = (key,)
        summary_record = dict(zip(key_columns, key))

        for column in source_columns:
            values = group[column].dropna()
            summary_record[column] = values.iloc[0] if not values.empty else None

        for repetition in range(1, REPETITIONS + 1):
            repetition_rows = group[group["repetition"] == repetition]
            if repetition_rows.empty:
                summary_record[f"rep_{repetition}_status"] = "PENDING"
                summary_record[f"rep_{repetition}_response"] = ""
                summary_record[f"rep_{repetition}_error"] = ""
            else:
                run = repetition_rows.iloc[-1]
                summary_record[f"rep_{repetition}_status"] = run.get("status", "")
                summary_record[f"rep_{repetition}_response"] = run.get("response", "")
                summary_record[f"rep_{repetition}_error"] = run.get("error", "")

        summary_rows.append(summary_record)

    summary_df = pd.DataFrame(summary_rows)
    return runs_df, summary_df


def save_dataset_workbook(spec: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    """Save detailed repetitions and an aggregated view in one workbook."""
    destination = output_path(spec)
    runs_df, summary_df = records_to_frames(records)

    temporary = destination.with_name(destination.stem + ".tmp.xlsx")
    with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
        runs_df.to_excel(writer, sheet_name="Independent_Runs", index=False)
        summary_df.to_excel(writer, sheet_name="Aggregated_Summary", index=False)
    temporary.replace(destination)
    return destination


# ============================================================================
# DATASET EXECUTION
# ============================================================================

def process_dataset(
    spec: dict[str, Any],
    model_call: Callable[[str], str],
) -> Path:
    """Run five separate, independent prompt requests for each source item."""
    input_file = find_input_file(spec)
    checkpoint = checkpoint_path(spec)

    records = read_checkpoint(checkpoint)
    completed_keys = {
        (
            record.get("source_sheet"),
            record.get("source_row"),
            int(record.get("repetition", 0)),
        )
        for record in records
        if record.get("status") in {"SUCCESS", "FAILED", "SKIPPED_EMPTY_CODE"}
    }

    print("\n" + "=" * 78)
    print(f"STARTING {spec['language'].upper()} - {spec['level'].upper()}")
    print("Input:", input_file.name)
    print("Independent requests per item:", REPETITIONS)
    print("Unit tests requested per response: 5")
    print("=" * 78)

    workbook = pd.ExcelFile(input_file)

    for sheet_name in workbook.sheet_names:
        dataframe = pd.read_excel(input_file, sheet_name=sheet_name)
        if dataframe.empty:
            print(f"Skipping empty sheet: {sheet_name}")
            continue

        code_column = find_code_column(dataframe, spec)
        if code_column is None:
            print(f"No source-code column found in sheet: {sheet_name}")
            print("Available columns:", list(dataframe.columns))
            continue

        print(f"Sheet: {sheet_name} | Rows: {len(dataframe)} | Code column: {code_column}")

        for position, (source_index, row) in enumerate(dataframe.iterrows(), start=1):
            source_row = position
            source_code = row.get(code_column)
            base_metadata = {
                "language": spec["language"],
                "difficulty": spec["level"],
                "source_file": input_file.name,
                "source_sheet": sheet_name,
                "source_row": source_row,
                "source_index": safe_cell_value(source_index),
                "code_column": code_column,
            }
            for column, value in row.items():
                base_metadata[str(column)] = safe_cell_value(value)

            if pd.isna(source_code) or not str(source_code).strip():
                for repetition in range(1, REPETITIONS + 1):
                    run_key = (sheet_name, source_row, repetition)
                    if run_key in completed_keys:
                        continue
                    record = {
                        **base_metadata,
                        "repetition": repetition,
                        "prompt": "",
                        "response": "",
                        "status": "SKIPPED_EMPTY_CODE",
                        "error": "Source-code field is empty.",
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    append_checkpoint(checkpoint, record)
                    records.append(record)
                    completed_keys.add(run_key)
                if SAVE_AFTER_EACH_REPETITION:
                    save_dataset_workbook(spec, records)
                continue

            for repetition in range(1, REPETITIONS + 1):
                run_key = (sheet_name, source_row, repetition)
                if run_key in completed_keys:
                    print(
                        f"Row {source_row}, repetition {repetition}: already saved; skipped."
                    )
                    continue

                prompt = make_prompt(spec["prompt"], str(source_code))
                print(
                    f"Row {source_row}/{len(dataframe)} | "
                    f"Independent request {repetition}/{REPETITIONS}"
                )

                try:
                    response = call_model_once(model_call, prompt)
                    status = "SUCCESS" if response else "FAILED"
                    error = "" if response else "The inference adapter returned an empty response."
                except Exception as exc:
                    response = ""
                    status = "FAILED"
                    error = f"{type(exc).__name__}: {exc}"

                record = {
                    **base_metadata,
                    "repetition": repetition,
                    "prompt": prompt,
                    "response": response,
                    "status": status,
                    "error": error,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }

                append_checkpoint(checkpoint, record)
                records.append(record)
                completed_keys.add(run_key)

                if SAVE_AFTER_EACH_REPETITION:
                    saved_path = save_dataset_workbook(spec, records)
                    print(f"Saved independent request {repetition}: {saved_path.name}")

                time.sleep(0.2)

    final_path = save_dataset_workbook(spec, records)
    print("Finished:", final_path)
    return final_path


# ============================================================================
# FINAL CONSOLIDATED WORKBOOK
# ============================================================================

def save_combined_workbook(specs: list[dict[str, Any]]) -> Path:
    """Combine all datasets into one workbook with detailed and summary sheets."""
    destination = OUTPUT_DIR / (
        f"All_Datasets_{REPETITIONS}_Independent_Repetitions.xlsx"
    )
    temporary = destination.with_name(destination.stem + ".tmp.xlsx")

    all_runs: list[pd.DataFrame] = []
    all_summaries: list[pd.DataFrame] = []

    for spec in specs:
        records = read_checkpoint(checkpoint_path(spec))
        if not records:
            continue
        runs_df, summary_df = records_to_frames(records)
        if not runs_df.empty:
            all_runs.append(runs_df)
        if not summary_df.empty:
            all_summaries.append(summary_df)

    combined_runs = pd.concat(all_runs, ignore_index=True) if all_runs else pd.DataFrame()
    combined_summary = pd.concat(all_summaries, ignore_index=True) if all_summaries else pd.DataFrame()

    with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
        combined_runs.to_excel(writer, sheet_name="All_Independent_Runs", index=False)
        combined_summary.to_excel(writer, sheet_name="All_Aggregated_Summary", index=False)

    temporary.replace(destination)
    return destination


def run_experiment(model_call: Callable[[str], str]) -> list[Path]:
    """Run all configured datasets using the supplied inference adapter."""
    validate_model_call(model_call)
    outputs: list[Path] = []

    for spec in DATASET_SPECS:
        try:
            outputs.append(process_dataset(spec, model_call))
        except FileNotFoundError as exc:
            print("Dataset not processed:", exc)

    combined_path = save_combined_workbook(DATASET_SPECS)
    outputs.append(combined_path)

    print("\nExperiment finished.")
    for path in outputs:
        if path.exists():
            print(f"{path.name}: {path.stat().st_size:,} bytes")
    return outputs


# ============================================================================
# EXECUTION
# ============================================================================

# Define a provider-specific adapter in the notebook, then run:
#
# def model_call(prompt: str) -> str:
#     messages = [{"role": "user", "content": prompt}]
#     response = YOUR_INFERENCE_FUNCTION(messages)
#     return response_text
#
# Each invocation must create a fresh request and must not carry chat history.
# output_files = run_experiment(model_call)
