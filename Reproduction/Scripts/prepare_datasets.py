from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "Datasets"

PYTHON_NAMES = {
    "easy": "easy_python_dataset.xlsx",
    "medium": "midum_python_dataset.xlsx",
    "hard": "Hard_python_dataset.xlsx",
}
JAVA_NAMES = {
    "easy": "Java_easy_dataset_cleaned.xlsx",
    "medium": "Java_midum_dataset.xlsx",
    "hard": "Java_Hard_dataset_cleaned.xlsx",
}

def read_records(path: Path, language: str) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("language", "").lower() != language:
        raise ValueError(f"{path.name} does not identify itself as {language}.")
    return payload["difficulty_levels"]

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the six Excel input workbooks required by the SQAI reproduction scripts."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/content"),
        help="Directory for the generated Excel workbooks (default: /content)."
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    python_levels = read_records(DATA_DIR / "Python_Dataset.json", "python")
    java_levels = read_records(DATA_DIR / "Java_Dataset.json", "java")

    written = []
    for level, filename in PYTHON_NAMES.items():
        records = python_levels[level]["records"]
        frame = pd.DataFrame(records)
        # Alias used by the model-agnostic generation runner and Pynguin.
        if "original_code_from_dataset" in frame.columns:
            frame["original_code"] = frame["original_code_from_dataset"]
        if "loc" in frame.columns:
            frame["source_loc"] = frame["loc"]
        path = args.output_dir / filename
        frame.to_excel(path, index=False, sheet_name="Dataset")
        written.append(path)

    for level, filename in JAVA_NAMES.items():
        records = java_levels[level]["records"]
        frame = pd.DataFrame(records)
        # Keep buggy_code_from_dataset intact: it is the source-code field
        # used by the Java generation pipeline and the Defects4J notebook.
        path = args.output_dir / filename
        frame.to_excel(path, index=False, sheet_name="Dataset")
        written.append(path)

    print("Prepared input workbooks:")
    for path in written:
        print(f" - {path} ({path.stat().st_size:,} bytes)")
    print("These files are derived from the JSON datasets in Datasets/.")

if __name__ == "__main__":
    main()
