# -*- coding: utf-8 -*-
"""Java focal-method coverage evaluation driven by Java_Dataset.json."""

# CELL 1 — Import libraries

import os
import re
import json
import time
import shutil
import subprocess
import traceback
import urllib.request
import xml.etree.ElementTree as ET

from pathlib import Path

from collections import Counter
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np

print("✓ Libraries imported successfully.")

# CELL 2 — LOCATE THE REPOSITORY DATASET

try:
    SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:
    SCRIPT_DIR = Path.cwd()

DATASET_CANDIDATES = [
    SCRIPT_DIR / "Java_Dataset.json",
    SCRIPT_DIR / "data" / "Java_Dataset.json",
    Path("/content/Java_Dataset.json"),
    Path("/content/data/Java_Dataset.json"),
]

DATASET_JSON = next(
    (candidate for candidate in DATASET_CANDIDATES if candidate.is_file()),
    None,
)

if DATASET_JSON is None:
    searched_paths = "\\n".join(str(path) for path in DATASET_CANDIDATES)
    raise FileNotFoundError(
        "Java_Dataset.json was not found. Place it beside this script "
        "or inside a data/ directory. Searched:\\n" + searched_paths
    )

print("=" * 70)
print("INPUT DATASET")
print("=" * 70)
print("JSON:", DATASET_JSON)
print("Size:", f"{DATASET_JSON.stat().st_size / (1024**2):.2f} MB")

# CELL 3 — COMPLETE JAVA FOCAL-METHOD-ONLY EVALUATION
#
# Reads all difficulty levels and generated-test records from Java_Dataset.json.
# Uses full Defects4J checkout ONLY as the execution environment.
# Coverage is extracted ONLY for the focal method.
#
# Mutation testing is NOT RUN.

# ============================================================
# 0. CONFIGURATION
# ============================================================

EXPECTED_REPEATS = None

# Fixed Defects4J version by default.
# Change to "b" for buggy-version execution.
EXECUTION_VERSION = "f"

MAX_WORKERS = 2

PROJECT_COMPILE_TIMEOUT = 600
DEFECTS4J_CLONE_TIMEOUT = 900
DEFECTS4J_INIT_TIMEOUT = 2400
CPANM_TIMEOUT = 1800
GENERATED_TEST_COMPILE_TIMEOUT = 180
JUNIT_TIMEOUT = 180
JACOCO_REPORT_TIMEOUT = 180
CHECKOUT_TIMEOUT = 300

WORK_ROOT = Path("/content/java_focal_coverage_all_files")
D4J_ROOT = WORK_ROOT / "defects4j"
CHECKOUT_ROOT = WORK_ROOT / "checkouts"
RUN_ROOT = WORK_ROOT / "runs"
TOOLS_ROOT = WORK_ROOT / "tools"

for folder in [
    WORK_ROOT,
    CHECKOUT_ROOT,
    RUN_ROOT,
    TOOLS_ROOT,
]:
    folder.mkdir(
        parents=True,
        exist_ok=True
    )

OUTPUT_XLSX = (
    WORK_ROOT
    / "Java_Focal_Method_Coverage_ALL_FILES.xlsx"
)

JACOCO_VERSION = "0.8.16"
JUNIT_VERSION = "1.14.3"

JACOCO_AGENT_URL = (
    "https://repo1.maven.org/maven2/org/jacoco/"
    "org.jacoco.agent/"
    f"{JACOCO_VERSION}/"
    f"org.jacoco.agent-{JACOCO_VERSION}-runtime.jar"
)

JACOCO_CLI_URL = (
    "https://repo1.maven.org/maven2/org/jacoco/"
    "org.jacoco.cli/"
    f"{JACOCO_VERSION}/"
    f"org.jacoco.cli-{JACOCO_VERSION}-nodeps.jar"
)

JUNIT_URL = (
    "https://repo1.maven.org/maven2/org/junit/platform/"
    "junit-platform-console-standalone/"
    f"{JUNIT_VERSION}/"
    f"junit-platform-console-standalone-{JUNIT_VERSION}.jar"
)

print("=" * 70)
print("CONFIGURATION")
print("=" * 70)
print("Execution version :", EXECUTION_VERSION)
print("Expected repeats   : DETECTED FROM JSON")
print("Workers            :", MAX_WORKERS)
print("Coverage scope     : FOCAL METHOD ONLY")
print("Mutation testing   : NOT RUN")


# ============================================================
# 1. HELPERS
# ============================================================

def run_cmd(
    command,
    cwd=None,
    timeout=120,
):
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def normalize_column_name(value):
    value = str(value).strip().lower()
    value = value.replace("\ufeff", "")
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def infer_tool_from_path(path_string):
    s = str(path_string).lower()

    if "/claude/" in s or "\\claude\\" in s:
        return "Claude"

    if "deepseek" in s:
        return "DeepSeek"

    if "/gpt/" in s or "\\gpt\\" in s or "/gpt" in s:
        return "GPT"

    if "gimini" in s or "gemini" in s:
        return "Gemini"

    if "qwen" in s:
        return "Qwen"

    if "evosuite" in s:
        return "EvoSuite"

    return "Unknown"


def infer_model_from_tool(
    tool,
    row=None,
):
    row = row if row is not None else {}

    if tool == "Claude":
        value = clean_text(
            row.get("model")
            if hasattr(row, "get")
            else ""
        )
        if value and "claude" in value.lower():
            return value
        return "claude-opus-4"

    if tool == "Qwen":
        value = clean_text(
            row.get("qwen_model")
            if hasattr(row, "get")
            else ""
        )
        return value or "Qwen"

    if tool == "EvoSuite":
        return "EvoSuite"

    if tool == "DeepSeek":
        return "DeepSeek"

    if tool == "GPT":
        return "GPT"

    if tool == "Gemini":
        return "Gemini"

    return "Unknown"


# ============================================================
# 2. SYSTEM DEPENDENCIES
# ============================================================

print("\n" + "=" * 70)
print("PREPARING JAVA / DEFECTS4J ENVIRONMENT")
print("=" * 70)

apt_script = r"""
apt-get update -qq &&
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    openjdk-11-jdk-headless \
    git \
    subversion \
    perl \
    cpanminus \
    make \
    build-essential \
    unzip \
    curl
"""

apt_result = subprocess.run(
    ["bash", "-lc", apt_script],
    capture_output=True,
    text=True,
)

if apt_result.returncode != 0:
    raise RuntimeError(
        "System dependency installation failed:\n"
        + (apt_result.stderr or "")[-6000:]
    )

java_candidates = [
    Path("/usr/lib/jvm/java-11-openjdk-amd64"),
    Path("/usr/lib/jvm/java-11-openjdk"),
]

JAVA_HOME_11 = next(
    (
        p
        for p in java_candidates
        if (p / "bin" / "java").exists()
    ),
    None,
)

if JAVA_HOME_11 is None:

    probe = subprocess.run(
        [
            "bash",
            "-lc",
            "find /usr/lib/jvm "
            "-maxdepth 1 "
            "-type d "
            "-name 'java-11*' "
            "| head -n 1",
        ],
        capture_output=True,
        text=True,
    )

    if probe.stdout.strip():
        JAVA_HOME_11 = Path(
            probe.stdout.strip()
        )

if JAVA_HOME_11 is None:
    raise RuntimeError(
        "Java 11 could not be located."
    )

os.environ["JAVA_HOME"] = str(
    JAVA_HOME_11
)

os.environ["PATH"] = (
    str(JAVA_HOME_11 / "bin")
    + os.pathsep
    + os.environ["PATH"]
)

java_version = run_cmd(
    ["java", "-version"],
    timeout=30,
)

print(
    "JAVA_HOME:",
    os.environ["JAVA_HOME"]
)

print(
    (java_version.stderr or java_version.stdout)[:1000]
)

subprocess.run(
    [
        "python",
        "-m",
        "pip",
        "install",
        "-q",
        "pandas==2.2.3",
        "openpyxl",
        "xlsxwriter",
    ],
    check=True,
)


# ============================================================
# 3. LOAD THE COMPLETE JSON DATASET
# ============================================================

print("\n" + "=" * 70)
print("LOADING JAVA DATASET")
print("=" * 70)

with DATASET_JSON.open("r", encoding="utf-8") as dataset_file:
    dataset = json.load(dataset_file)

if not isinstance(dataset, dict):
    raise TypeError("The dataset root must be a JSON object.")

difficulty_levels = dataset.get("difficulty_levels")
if not isinstance(difficulty_levels, dict) or not difficulty_levels:
    raise ValueError(
        "The dataset must contain a non-empty 'difficulty_levels' object."
    )

all_records = []
file_audit_rows = []
total_dataset_records = 0
total_generated_tests = 0

generated_test_pattern = re.compile(
    r"^result_(\d+)_generated_unit_test$"
)

provider_label_pattern = re.compile(
    r"Provider/model label:\s*([^/\s]+)/([^\r\n]+)",
    flags=re.IGNORECASE,
)

def parse_provider_and_model(record, test_code):
    provider = clean_text(record.get("provider"))
    model = clean_text(record.get("model"))

    if provider and model:
        return provider, model

    match = provider_label_pattern.search(test_code or "")
    if match:
        parsed_provider = match.group(1).strip()
        parsed_model = match.group(2).strip()
        provider = provider or parsed_provider
        model = model or parsed_model

    if not provider:
        provider = "Unknown"
    if not model:
        model = infer_model_from_tool(provider.title(), record)

    return provider, model


for difficulty_key, level_data in difficulty_levels.items():
    if not isinstance(level_data, dict):
        print("Skipping invalid difficulty entry:", difficulty_key)
        continue

    level_records = level_data.get("records", [])
    if not isinstance(level_records, list):
        print("Skipping invalid records collection:", difficulty_key)
        continue

    level_record_count = 0
    level_generated_count = 0
    level_missing_count = 0

    for record_index, record in enumerate(level_records):
        if not isinstance(record, dict):
            continue

        total_dataset_records += 1
        level_record_count += 1

        result_fields = []
        for field_name, field_value in record.items():
            match = generated_test_pattern.match(str(field_name))
            if match and clean_text(field_value):
                result_fields.append((int(match.group(1)), field_name, field_value))

        result_fields.sort(key=lambda item: item[0])

        if not result_fields:
            level_missing_count += 1
            continue

        for repeat_number, field_name, test_code in result_fields:
            provider, model = parse_provider_and_model(record, test_code)

            row = {
                "provider": provider,
                "model": model,
                "difficulty": clean_text(record.get("difficulty")) or str(difficulty_key).title(),
                "sample_index": record.get("sample_index"),
                "project_id": clean_text(record.get("project_id")),
                "bug_id": record.get("bug_id"),
                "task_id": clean_text(record.get("task_id")),
                "qualified_class_name": clean_text(record.get("qualified_class_name")),
                "function_name": clean_text(record.get("function_name")),
                "test_no": repeat_number,
                "test_case": clean_text(test_code),
                "source_file": DATASET_JSON.name,
                "source_sheet": str(difficulty_key),
                "source_path": (
                    f"difficulty_levels/{difficulty_key}/records/{record_index}"
                ),
                "input_schema": field_name,
                "source_result_status": clean_text(
                    record.get(f"result_{repeat_number}_status")
                ),
                "cyclomatic_complexity": record.get("cyclomatic_complexity"),
                "loc": record.get("loc"),
            }

            required_fields = [
                "project_id",
                "bug_id",
                "task_id",
                "qualified_class_name",
                "function_name",
            ]
            missing_fields = [
                field for field in required_fields if not clean_text(row.get(field))
            ]
            if missing_fields:
                raise ValueError(
                    f"Missing required metadata fields in "
                    f"{row['source_path']}: {missing_fields}"
                )

            all_records.append(row)
            level_generated_count += 1
            total_generated_tests += 1

    file_audit_rows.append(
        {
            "source_file": DATASET_JSON.name,
            "source_path": f"difficulty_levels/{difficulty_key}",
            "tool": "Multiple providers or tools",
            "sheets": 1,
            "rows_read": level_record_count,
            "records_extracted": level_generated_count,
            "records_without_generated_tests": level_missing_count,
            "status": "READ",
        }
    )

records_df = pd.DataFrame(all_records)

if records_df.empty:
    raise RuntimeError(
        "No generated unit tests were found in Java_Dataset.json. "
        "Expected fields such as result_1_generated_unit_test."
    )

EXPECTED_REPEATS = int(
    pd.to_numeric(records_df["test_no"], errors="coerce").max()
)

print("=" * 70)
print("INPUT AUDIT")
print("=" * 70)
print("Dataset records:", total_dataset_records)
print("Generated test records:", total_generated_tests)
print("Difficulty levels:", ", ".join(str(level) for level in difficulty_levels))
print("Detected repeat indices:", sorted(records_df["test_no"].unique().tolist()))
print("Expected repeat count:", EXPECTED_REPEATS)

display(pd.DataFrame(file_audit_rows))

print("\nProvider distribution:")
display(
    records_df["provider"]
    .value_counts(dropna=False)
    .rename_axis("provider")
    .reset_index(name="records")
)

print("\nDifficulty distribution:")
display(
    records_df["difficulty"]
    .value_counts(dropna=False)
    .rename_axis("difficulty")
    .reset_index(name="records")
)


# ============================================================
# 6. DEFECTS4J INSTALLATION
# ============================================================

print("\n" + "=" * 70)
print("SETTING UP DEFECTS4J")
print("=" * 70)

D4J_BIN = (
    D4J_ROOT
    / "framework"
    / "bin"
    / "defects4j"
)

if not D4J_BIN.exists():

    if D4J_ROOT.exists():
        shutil.rmtree(
            D4J_ROOT,
            ignore_errors=True
        )

    print(
        "Cloning Defects4J..."
    )

    clone = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "https://github.com/rjust/defects4j.git",
            str(D4J_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=DEFECTS4J_CLONE_TIMEOUT,
    )

    if clone.returncode != 0:

        raise RuntimeError(
            "Defects4J clone failed:\n"
            + (
                clone.stderr
                or ""
            )[-7000:]
        )

# ------------------------------------------------------------
# Perl dependencies
# ------------------------------------------------------------

perl_probe = subprocess.run(
    [
        "perl",
        "-MJSON",
        "-MTemplate",
        "-MFile::Which",
        "-e",
        "1",
    ],
    cwd=str(D4J_ROOT),
    capture_output=True,
    text=True,
    timeout=60,
)

if perl_probe.returncode != 0:

    print(
        "Installing Perl dependencies "
        "(up to 30 minutes)..."
    )

    perl_install = subprocess.run(
        [
            "cpanm",
            "--installdeps",
            ".",
        ],
        cwd=str(D4J_ROOT),
        capture_output=True,
        text=True,
        timeout=CPANM_TIMEOUT,
    )

    if perl_install.returncode != 0:

        raise RuntimeError(
            "Defects4J Perl dependency "
            "installation failed:\n"
            + (
                perl_install.stderr
                or ""
            )[-7000:]
        )

else:

    print(
        "✓ Perl dependencies already available."
    )

os.environ["PATH"] = (
    str(
        D4J_ROOT
        / "framework"
        / "bin"
    )
    + os.pathsep
    + os.environ["PATH"]
)

framework_probe = run_cmd(
    [
        "defects4j",
        "info",
        "-p",
        "Lang",
    ],
    cwd=str(D4J_ROOT),
    timeout=60,
)

if framework_probe.returncode != 0:

    print(
        "Initializing Defects4J "
        "(up to 40 minutes)..."
    )

    init_result = subprocess.run(
        [
            "./init.sh"
        ],
        cwd=str(D4J_ROOT),
        capture_output=True,
        text=True,
        timeout=DEFECTS4J_INIT_TIMEOUT,
    )

    if init_result.returncode != 0:

        raise RuntimeError(
            "Defects4J initialization failed:\n"
            + (
                init_result.stderr
                or ""
            )[-7000:]
        )

else:

    print(
        "✓ Defects4J already initialized."
    )


# ============================================================
# 7. DOWNLOAD JACOCO / JUNIT
# ============================================================

def download_tool(
    url,
    target,
):

    target = Path(
        target
    )

    if (
        target.exists()
        and target.stat().st_size > 10000
    ):
        return

    print(
        "Downloading:",
        target.name
    )

    urllib.request.urlretrieve(
        url,
        str(target)
    )

    if not target.exists():
        raise FileNotFoundError(
            target
        )


JACOCO_AGENT = (
    TOOLS_ROOT
    / f"jacoco-agent-{JACOCO_VERSION}.jar"
)

JACOCO_CLI = (
    TOOLS_ROOT
    / f"jacoco-cli-{JACOCO_VERSION}.jar"
)

JUNIT_CONSOLE = (
    TOOLS_ROOT
    / f"junit-platform-console-{JUNIT_VERSION}.jar"
)

download_tool(
    JACOCO_AGENT_URL,
    JACOCO_AGENT,
)

download_tool(
    JACOCO_CLI_URL,
    JACOCO_CLI,
)

download_tool(
    JUNIT_URL,
    JUNIT_CONSOLE,
)

print(
    "✓ JaCoCo and JUnit ready."
)


# ============================================================
# 8. SAFE PROJECT CHECKOUT + COMPILE
# ============================================================

CHECKOUT_CACHE = {}
PROJECT_LOCKS = {}
PROJECT_LOCKS_GUARD = Lock()


def project_lock(
    cache_key
):
    with PROJECT_LOCKS_GUARD:

        if cache_key not in PROJECT_LOCKS:

            PROJECT_LOCKS[
                cache_key
            ] = Lock()

        return PROJECT_LOCKS[
            cache_key
        ]


def d4j_export(
    project_dir,
    prop,
):

    result = run_cmd(
        [
            "defects4j",
            "export",
            "-p",
            prop,
        ],
        cwd=str(project_dir),
        timeout=60,
    )

    if result.returncode != 0:

        raise RuntimeError(
            f"Defects4J export failed "
            f"for {prop}:\n"
            + (
                result.stderr
                or ""
            )[-5000:]
        )

    return (
        result.stdout
        or ""
    ).strip()


def checkout_project(
    project_id,
    bug_id,
):

    project_id = clean_text(
        project_id
    )

    bug_id = clean_text(
        bug_id
    )

    if not project_id or not bug_id:

        raise ValueError(
            "Missing project_id or bug_id."
        )

    cache_key = (
        f"{project_id}_"
        f"{bug_id}_"
        f"{EXECUTION_VERSION}"
    )

    if cache_key in CHECKOUT_CACHE:

        return CHECKOUT_CACHE[
            cache_key
        ]

    lock = project_lock(
        cache_key
    )

    with lock:

        if cache_key in CHECKOUT_CACHE:

            return CHECKOUT_CACHE[
                cache_key
            ]

        workdir = (
            CHECKOUT_ROOT
            / cache_key
        )

        config_marker = (
            workdir
            / ".defects4j.config"
        )

        compile_marker = (
            workdir
            / ".focal_compiled"
        )

        # Remove stale partial checkouts.
        if (
            workdir.exists()
            and not config_marker.exists()
        ):

            print(
                "Removing stale checkout:",
                workdir,
                flush=True,
            )

            shutil.rmtree(
                workdir,
                ignore_errors=True
            )

        try:

            # ----------------------------------------------
            # Checkout
            # ----------------------------------------------

            if not workdir.exists():

                version = (
                    f"{bug_id}"
                    f"{EXECUTION_VERSION}"
                )

                checkout_result = run_cmd(
                    [
                        "defects4j",
                        "checkout",
                        "-p",
                        project_id,
                        "-v",
                        version,
                        "-w",
                        str(workdir),
                    ],
                    timeout=CHECKOUT_TIMEOUT,
                )

                if (
                    checkout_result.returncode != 0
                    or not config_marker.exists()
                ):

                    raise RuntimeError(
                        f"Checkout failed for "
                        f"{project_id}-{bug_id}"
                        f"{EXECUTION_VERSION}:\n"
                        + (
                            checkout_result.stderr
                            or ""
                        )[-6000:]
                    )

            # ----------------------------------------------
            # Compile once
            # ----------------------------------------------

            if not compile_marker.exists():

                compile_result = run_cmd(
                    [
                        "defects4j",
                        "compile",
                    ],
                    cwd=str(workdir),
                    timeout=PROJECT_COMPILE_TIMEOUT,
                )

                if compile_result.returncode != 0:

                    raise RuntimeError(
                        f"Project compile failed for "
                        f"{project_id}-{bug_id}"
                        f"{EXECUTION_VERSION}:\n"
                        + (
                            compile_result.stdout
                            or ""
                        )[-5000:]
                        + "\n"
                        + (
                            compile_result.stderr
                            or ""
                        )[-5000:]
                    )

                compile_marker.write_text(
                    "compiled",
                    encoding="utf-8",
                )

            CHECKOUT_CACHE[
                cache_key
            ] = workdir

            return workdir

        except Exception:

            if workdir.exists():

                shutil.rmtree(
                    workdir,
                    ignore_errors=True
                )

            CHECKOUT_CACHE.pop(
                cache_key,
                None
            )

            raise


# ============================================================
# 9. JAVA SOURCE / FOCAL METHOD
# ============================================================

def locate_java_source(
    project_dir,
    qualified_class_name,
    source_dir,
):

    relative = (
        str(
            qualified_class_name
        )
        .replace(
            ".",
            "/"
        )
        + ".java"
    )

    direct = (
        Path(project_dir)
        / source_dir
        / relative
    )

    if direct.exists():
        return direct

    source_root = (
        Path(project_dir)
        / source_dir
    )

    if source_root.exists():

        filename = Path(
            relative
        ).name

        matches = list(
            source_root.rglob(
                filename
            )
        )

        if matches:
            return matches[0]

    return None


def matching_brace(
    text,
    open_index
):

    depth = 0

    in_string = None
    escaped = False
    line_comment = False
    block_comment = False

    i = open_index

    while i < len(text):

        ch = text[i]

        nxt = (
            text[i + 1]
            if i + 1 < len(text)
            else ""
        )

        if line_comment:

            if ch == "\n":
                line_comment = False

            i += 1
            continue

        if block_comment:

            if (
                ch == "*"
                and nxt == "/"
            ):

                block_comment = False
                i += 2
                continue

            i += 1
            continue

        if in_string:

            if escaped:

                escaped = False

            elif ch == "\\":
                escaped = True

            elif ch == in_string:
                in_string = None

            i += 1
            continue

        if (
            ch == "/"
            and nxt == "/"
        ):

            line_comment = True
            i += 2
            continue

        if (
            ch == "/"
            and nxt == "*"
        ):

            block_comment = True
            i += 2
            continue

        if ch in (
            '"',
            "'",
        ):

            in_string = ch
            i += 1
            continue

        if ch == "{":

            depth += 1

        elif ch == "}":

            depth -= 1

            if depth == 0:
                return i

        i += 1

    return None


def likely_declaration(
    text,
    start
):

    line_start = (
        text.rfind(
            "\n",
            0,
            start
        )
        + 1
    )

    prefix = (
        text[
            line_start:
            start
        ]
        .strip()
    )

    # Eliminate obvious method invocations.
    if prefix.endswith("."):
        return False

    if prefix.endswith(
        (
            "return",
            "throw",
            "new",
        )
    ):
        return False

    if "=" in prefix:
        return False

    if any(
        word in prefix.split()
        for word in [
            "public",
            "private",
            "protected",
            "static",
            "final",
            "abstract",
            "synchronized",
            "native",
        ]
    ):
        return True

    # Package-private methods usually have a return type before
    # the method name.
    return bool(
        re.search(
            r"(?:[A-Za-z_$][\w$<>\[\]]*\s+)+$",
            prefix,
        )
    )


def find_method_candidates(
    source_text,
    method_name,
):

    pattern = re.compile(
        rf"\b{re.escape(str(method_name))}\s*\("
    )

    candidates = []

    for match in pattern.finditer(
        source_text
    ):

        if not likely_declaration(
            source_text,
            match.start()
        ):
            continue

        depth = 0
        close_paren = None

        i = match.end() - 1

        while i < len(source_text):

            if source_text[i] == "(":
                depth += 1

            elif source_text[i] == ")":

                depth -= 1

                if depth == 0:

                    close_paren = i
                    break

            i += 1

        if close_paren is None:
            continue

        brace = source_text.find(
            "{",
            close_paren
        )

        if brace == -1:
            continue

        end = matching_brace(
            source_text,
            brace
        )

        if end is None:
            continue

        start_line = (
            source_text.count(
                "\n",
                0,
                match.start()
            )
            + 1
        )

        end_line = (
            source_text.count(
                "\n",
                0,
                end
            )
            + 1
        )

        candidates.append(
            {
                "start_line":
                    start_line,

                "end_line":
                    end_line,
            }
        )

    return candidates


def locate_focal_method(
    project_dir,
    qualified_class_name,
    function_name,
    source_dir,
):

    source_path = locate_java_source(
        project_dir,
        qualified_class_name,
        source_dir,
    )

    if source_path is None:

        return {
            "status":
                "SOURCE_FILE_NOT_FOUND",
            "source_path":
                None,
        }

    text = source_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    candidates = find_method_candidates(
        text,
        function_name,
    )

    if not candidates:

        return {
            "status":
                "FOCAL_METHOD_NOT_FOUND",
            "source_path":
                str(source_path),
        }

    return {
        "status":
            "OK",
        "source_path":
            str(source_path),
        "candidates":
            candidates,
    }


# ============================================================
# 10. GENERATED TEST PREPARATION
# ============================================================

def focal_package(
    qualified_class_name
):

    value = str(
        qualified_class_name
    ).strip()

    if "." not in value:
        return ""

    return value.rsplit(
        ".",
        1
    )[0]


def parse_test_class(
    test_code
):

    text = str(
        test_code
    )

    package_match = re.search(
        r"\bpackage\s+"
        r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
        r"\s*;",
        text,
    )

    package_name = (
        package_match.group(1)
        if package_match
        else ""
    )

    class_names = re.findall(
        r"\b(?:public\s+)?"
        r"(?:final\s+|abstract\s+)?"
        r"class\s+"
        r"([A-Za-z_$][A-Za-z0-9_$]*)",
        text,
    )

    if not class_names:

        return (
            package_name,
            None
        )

    preferred = [
        name
        for name in class_names
        if name.lower().endswith(
            (
                "test",
                "tests",
            )
        )
    ]

    class_name = (
        preferred[0]
        if preferred
        else class_names[0]
    )

    return (
        package_name,
        class_name
    )


def prepare_test_source(
    test_code,
    qualified_class_name,
    fallback_name,
):

    text = str(
        test_code
    )

    package_name, class_name = (
        parse_test_class(
            text
        )
    )

    # If a complete class already exists, preserve its logic.
    if class_name:

        # If package is missing, add the focal package
        # before imports. This is a harness adjustment,
        # not a change to the generated test logic.
        if not package_name:

            package_name = focal_package(
                qualified_class_name
            )

            if package_name:

                text = (
                    f"package {package_name};\n\n"
                    + text
                )

        fqcn = (
            f"{package_name}.{class_name}"
            if package_name
            else class_name
        )

        return (
            text,
            fqcn,
            class_name,
            False,
        )

    # --------------------------------------------------------
    # Snippet without enclosing class.
    # Wrap only the harness around the generated test methods.
    # --------------------------------------------------------

    package_match = re.search(
        r"^\s*package\s+"
        r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
        r"\s*;\s*",
        text,
        flags=re.MULTILINE,
    )

    embedded_package = (
        package_match.group(1)
        if package_match
        else ""
    )

    package_name = (
        embedded_package
        or focal_package(
            qualified_class_name
        )
    )

    imports = re.findall(
        r"^\s*import\s+[^;]+;\s*$",
        text,
        flags=re.MULTILINE,
    )

    body = re.sub(
        r"^\s*package\s+"
        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
        r"\s*;\s*",
        "",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    body = re.sub(
        r"^\s*import\s+[^;]+;\s*$",
        "",
        body,
        flags=re.MULTILINE,
    ).strip()

    safe_name = re.sub(
        r"[^A-Za-z0-9_$]",
        "_",
        str(fallback_name),
    )

    class_name = (
        "GeneratedFocalTest_"
        + safe_name[:50]
    )

    prefix = ""

    if package_name:

        prefix += (
            f"package {package_name};\n\n"
        )

    if imports:

        prefix += (
            "\n".join(
                imports
            )
            + "\n\n"
        )

    wrapped = (
        prefix
        + f"public class {class_name} {{\n"
        + body
        + "\n}\n"
    )

    fqcn = (
        f"{package_name}.{class_name}"
        if package_name
        else class_name
    )

    return (
        wrapped,
        fqcn,
        class_name,
        True,
    )


# ============================================================
# 11. JUNIT SUMMARY
# ============================================================

def junit_summary(
    output
):

    result = {
        "tests_found":
            0,
        "tests_started":
            0,
        "tests_successful":
            0,
        "tests_failed":
            0,
        "tests_aborted":
            0,
        "tests_skipped":
            0,
    }

    patterns = {

        "tests_found":
            r"(\d+)\s+tests?\s+found",

        "tests_started":
            r"(\d+)\s+tests?\s+started",

        "tests_successful":
            r"(\d+)\s+tests?\s+successful",

        "tests_failed":
            r"(\d+)\s+tests?\s+failed",

        "tests_aborted":
            r"(\d+)\s+tests?\s+aborted",

        "tests_skipped":
            r"(\d+)\s+tests?\s+skipped",
    }

    for key, pattern in patterns.items():

        match = re.search(
            pattern,
            output,
            flags=re.IGNORECASE,
        )

        if match:

            result[key] = int(
                match.group(1)
            )

    return result


# ============================================================
# 12. EXECUTE ONE RECORD
# ============================================================

def execute_record(
    row
):

    started = time.perf_counter()

    result = {
        **{
            key:
                row.get(key)
            for key in [
                "provider",
                "model",
                "difficulty",
                "sample_index",
                "project_id",
                "bug_id",
                "task_id",
                "qualified_class_name",
                "function_name",
                "test_no",
                "source_file",
                "source_sheet",
                "input_schema",
                "source_path",
            ]
        },

        "compile_status":
            None,

        "test_status":
            None,

        "coverage_status":
            None,

        "coverage_error":
            None,

        "tests_found":
            0,

        "tests_started":
            0,

        "tests_successful":
            0,

        "tests_failed":
            0,

        "tests_aborted":
            0,

        "tests_skipped":
            0,

        "executability_rate_percent":
            None,

        "test_pass_rate_percent":
            None,

        "method_covered":
            None,

        "method_coverage_percent":
            None,

        "line_coverage_percent":
            None,

        "branch_coverage_percent":
            None,

        "instruction_coverage_percent":
            None,

        "focal_line_total":
            None,

        "focal_line_covered":
            None,

        "focal_branch_total":
            None,

        "focal_branch_covered":
            None,

        "focal_method_start_line":
            None,

        "focal_method_end_line":
            None,

        "coverage_method_match":
            None,

        "focal_source_path":
            None,

        "stdout_tail":
            "",

        "stderr_tail":
            "",

        "evaluation_time_seconds":
            None,
    }

    test_code = clean_text(
        row.get("test_case")
    )

    if not test_code:

        result[
            "compile_status"
        ] = "NOT_RUN"

        result[
            "test_status"
        ] = "NO_GENERATED_TEST"

        result[
            "coverage_status"
        ] = "NO_GENERATED_TEST"

        result[
            "evaluation_time_seconds"
        ] = (
            time.perf_counter()
            - started
        )

        return result

    try:

        project_dir = checkout_project(
            row.get("project_id"),
            row.get("bug_id"),
        )

        source_dir = d4j_export(
            project_dir,
            "dir.src.classes",
        )

        binary_dir = d4j_export(
            project_dir,
            "dir.bin.classes",
        )

        test_classpath = d4j_export(
            project_dir,
            "cp.test",
        )

        focal = locate_focal_method(
            project_dir,
            row.get(
                "qualified_class_name"
            ),
            row.get(
                "function_name"
            ),
            source_dir,
        )

        result[
            "focal_source_path"
        ] = focal.get(
            "source_path"
        )

        if focal.get(
            "status"
        ) != "OK":

            result[
                "coverage_status"
            ] = focal.get(
                "status"
            )

            return result

        candidates = focal[
            "candidates"
        ]

        run_name = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            (
                f"{row.get('project_id')}_"
                f"{row.get('bug_id')}_"
                f"{row.get('function_name')}_"
                f"{row.get('test_no')}_"
                f"{time.time_ns()}"
            ),
        )

        run_dir = (
            RUN_ROOT
            / run_name
        )

        run_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        prepared_source, fqcn, class_name, wrapped = (
            prepare_test_source(
                test_code,
                row.get(
                    "qualified_class_name"
                ),
                f"{row.get('function_name')}_"
                f"{row.get('test_no')}",
            )
        )

        generated_src_dir = (
            run_dir
            / "generated_test_src"
        )

        package_name = (
            fqcn.rsplit(
                ".",
                1
            )[0]
            if "." in fqcn
            else ""
        )

        package_dir = (
            Path(
                *package_name.split(".")
            )
            if package_name
            else Path()
        )

        test_source_dir = (
            generated_src_dir
            / package_dir
        )

        test_source_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        test_java = (
            test_source_dir
            / f"{class_name}.java"
        )

        test_java.write_text(
            prepared_source,
            encoding="utf-8",
        )

        generated_classes = (
            run_dir
            / "generated_classes"
        )

        generated_classes.mkdir(
            parents=True,
            exist_ok=True
        )

        compile_cp = (
            str(test_classpath)
            + os.pathsep
            + str(JUNIT_CONSOLE)
        )

        compile_result = run_cmd(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-cp",
                compile_cp,
                "-d",
                str(generated_classes),
                str(test_java),
            ],
            timeout=GENERATED_TEST_COMPILE_TIMEOUT,
        )

        result[
            "stdout_tail"
        ] = (
            compile_result.stdout
            or ""
        )[-5000:]

        result[
            "stderr_tail"
        ] = (
            compile_result.stderr
            or ""
        )[-5000:]

        if compile_result.returncode != 0:

            result[
                "compile_status"
            ] = "COMPILE_FAILED"

            result[
                "test_status"
            ] = "COMPILE_FAILED"

            result[
                "coverage_status"
            ] = "NOT_RUN"

            return result

        result[
            "compile_status"
        ] = "COMPILE_OK"

        execution_file = (
            run_dir
            / "jacoco.exec"
        )

        runtime_cp = (
            str(test_classpath)
            + os.pathsep
            + str(generated_classes)
        )

        junit_result = run_cmd(
            [
                "java",

                (
                    f"-javaagent:"
                    f"{JACOCO_AGENT}"
                    f"=destfile="
                    f"{execution_file}"
                    f",append=false"
                ),

                "-jar",
                str(JUNIT_CONSOLE),

                "--class-path",
                runtime_cp,

                "--select-class",
                fqcn,

                "--details=summary",
                "--disable-ansi-colors",
            ],
            timeout=JUNIT_TIMEOUT,
        )

        junit_output = (
            (
                junit_result.stdout
                or ""
            )
            + "\n"
            + (
                junit_result.stderr
                or ""
            )
        )

        summary = junit_summary(
            junit_output
        )

        result.update(
            summary
        )

        if (
            result["tests_found"]
            > 0
        ):

            result[
                "executability_rate_percent"
            ] = (
                result["tests_started"]
                / result["tests_found"]
                * 100.0
            )

        if (
            result["tests_started"]
            > 0
        ):

            result[
                "test_pass_rate_percent"
            ] = (
                result["tests_successful"]
                / result["tests_started"]
                * 100.0
            )

        result[
            "stdout_tail"
        ] = (
            junit_result.stdout
            or ""
        )[-5000:]

        result[
            "stderr_tail"
        ] = (
            junit_result.stderr
            or ""
        )[-5000:]

        if result[
            "tests_failed"
        ] > 0:

            result[
                "test_status"
            ] = "FAIL"

        elif result[
            "tests_successful"
        ] > 0:

            result[
                "test_status"
            ] = "PASS"

        elif result[
            "tests_aborted"
        ] > 0:

            result[
                "test_status"
            ] = "ABORTED"

        elif result[
            "tests_found"
        ] == 0:

            result[
                "test_status"
            ] = "NO_TESTS_DISCOVERED"

        else:

            result[
                "test_status"
            ] = "NO_TEST_RESULT"

        # ----------------------------------------------------
        # JaCoCo report
        # ----------------------------------------------------

        if not execution_file.exists():

            result[
                "coverage_status"
            ] = "NO_EXEC_FILE"

            return result

        xml_file = (
            run_dir
            / "jacoco.xml"
        )

        class_directory = (
            Path(project_dir)
            / binary_dir
        )

        source_directory = (
            Path(project_dir)
            / source_dir
        )

        report_result = run_cmd(
            [
                "java",
                "-jar",
                str(JACOCO_CLI),
                "report",
                str(execution_file),
                "--classfiles",
                str(class_directory),
                "--sourcefiles",
                str(source_directory),
                "--xml",
                str(xml_file),
            ],
            timeout=JACOCO_REPORT_TIMEOUT,
        )

        if (
            report_result.returncode != 0
            or not xml_file.exists()
        ):

            result[
                "coverage_status"
            ] = "JACOCO_REPORT_FAILED"

            result[
                "coverage_error"
            ] = (
                (
                    report_result.stdout
                    or ""
                )
                + "\n"
                + (
                    report_result.stderr
                    or ""
                )
            )[-7000:]

            return result

        root = ET.parse(
            xml_file
        ).getroot()

        target_internal = (
            str(
                row.get(
                    "qualified_class_name"
                )
            )
            .replace(
                ".",
                "/"
            )
        )

        target_class = None

        # JaCoCo class/@name is normally already
        # the full internal class name.
        for package_node in root.findall(
            "package"
        ):

            package_name = (
                package_node.attrib.get(
                    "name",
                    ""
                )
            )

            for class_node in (
                package_node.findall(
                    "class"
                )
            ):

                class_name_xml = (
                    class_node.attrib.get(
                        "name",
                        ""
                    )
                )

                full_name = (
                    f"{package_name}/"
                    f"{class_name_xml}"
                    if (
                        package_name
                        and not class_name_xml.startswith(
                            package_name + "/"
                        )
                    )
                    else class_name_xml
                )

                if (
                    class_name_xml
                    == target_internal
                    or full_name
                    == target_internal
                ):

                    target_class = (
                        class_node
                    )
                    break

            if target_class is not None:
                break

        if target_class is None:

            result[
                "coverage_status"
            ] = (
                "FOCAL_CLASS_NOT_IN_JACOCO"
            )

            return result

        # ----------------------------------------------------
        # Method matching.
        # Exact descriptor is not available in the input, so
        # method name is matched and source line resolves overloads.
        # ----------------------------------------------------

        method_nodes = [
            method
            for method
            in target_class.findall(
                "method"
            )
            if method.attrib.get(
                "name"
            )
            == str(
                row.get(
                    "function_name"
                )
            )
        ]

        if not method_nodes:

            result[
                "coverage_status"
            ] = (
                "FOCAL_METHOD_NOT_IN_JACOCO"
            )

            return result

        chosen_method = (
            method_nodes[0]
        )

        chosen_candidate = (
            candidates[0]
        )

        best_distance = float(
            "inf"
        )

        for method_node in method_nodes:

            try:
                method_line = int(
                    method_node.attrib.get(
                        "line",
                        "0"
                    )
                )
            except Exception:
                method_line = None

            if method_line is None:
                continue

            nearest_candidate = min(
                candidates,
                key=lambda candidate:
                    abs(
                        candidate[
                            "start_line"
                        ]
                        - method_line
                    )
            )

            distance = abs(
                nearest_candidate[
                    "start_line"
                ]
                - method_line
            )

            if distance < best_distance:

                best_distance = distance
                chosen_method = (
                    method_node
                )
                chosen_candidate = (
                    nearest_candidate
                )

        result[
            "coverage_method_match"
        ] = (
            "NAME_AND_LINE"
            if len(method_nodes) > 1
            else "NAME"
        )

        result[
            "focal_method_start_line"
        ] = chosen_candidate[
            "start_line"
        ]

        result[
            "focal_method_end_line"
        ] = chosen_candidate[
            "end_line"
        ]

        # ----------------------------------------------------
        # DIRECT METHOD COUNTERS
        #
        # This is the key change:
        # coverage is taken directly from the selected
        # <method> element rather than the whole class.
        # ----------------------------------------------------

        counters = {}

        for counter in chosen_method.findall(
            "counter"
        ):

            counter_type = counter.attrib.get(
                "type"
            )

            if counter_type:
                counters[
                    counter_type
                ] = counter.attrib

        if not counters:

            result[
                "coverage_status"
            ] = (
                "FOCAL_METHOD_HAS_NO_COUNTERS"
            )

            return result

        if "METHOD" in counters:

            covered = int(
                counters[
                    "METHOD"
                ].get(
                    "covered",
                    "0"
                )
            )

            missed = int(
                counters[
                    "METHOD"
                ].get(
                    "missed",
                    "0"
                )
            )

            total = (
                covered
                + missed
            )

            result[
                "method_covered"
            ] = (
                covered > 0
            )

            result[
                "method_coverage_percent"
            ] = (
                covered
                / total
                * 100.0
                if total > 0
                else None
            )

        if "INSTRUCTION" in counters:

            covered = int(
                counters[
                    "INSTRUCTION"
                ].get(
                    "covered",
                    "0"
                )
            )

            missed = int(
                counters[
                    "INSTRUCTION"
                ].get(
                    "missed",
                    "0"
                )
            )

            total = (
                covered
                + missed
            )

            result[
                "instruction_coverage_percent"
            ] = (
                covered
                / total
                * 100.0
                if total > 0
                else None
            )

        if "LINE" in counters:

            covered = int(
                counters[
                    "LINE"
                ].get(
                    "covered",
                    "0"
                )
            )

            missed = int(
                counters[
                    "LINE"
                ].get(
                    "missed",
                    "0"
                )
            )

            total = (
                covered
                + missed
            )

            result[
                "focal_line_total"
            ] = total

            result[
                "focal_line_covered"
            ] = covered

            result[
                "line_coverage_percent"
            ] = (
                covered
                / total
                * 100.0
                if total > 0
                else None
            )

        if "BRANCH" in counters:

            covered = int(
                counters[
                    "BRANCH"
                ].get(
                    "covered",
                    "0"
                )
            )

            missed = int(
                counters[
                    "BRANCH"
                ].get(
                    "missed",
                    "0"
                )
            )

            total = (
                covered
                + missed
            )

            result[
                "focal_branch_total"
            ] = total

            result[
                "focal_branch_covered"
            ] = covered

            result[
                "branch_coverage_percent"
            ] = (
                covered
                / total
                * 100.0
                if total > 0
                else None
            )

        else:

            result[
                "branch_coverage_percent"
            ] = None

        result[
            "coverage_status"
        ] = "OK"

        return result

    except subprocess.TimeoutExpired:

        result[
            "test_status"
        ] = "TIMEOUT"

        result[
            "coverage_status"
        ] = "TIMEOUT"

        return result

    except Exception as exc:

        result[
            "test_status"
        ] = "EVALUATOR_ERROR"

        result[
            "coverage_status"
        ] = "EVALUATOR_ERROR"

        result[
            "coverage_error"
        ] = (
            str(exc)
            + "\n"
            + traceback.format_exc()[-7000:]
        )

        return result

    finally:

        result[
            "evaluation_time_seconds"
        ] = (
            time.perf_counter()
            - started
        )


# ============================================================
# 13. EXECUTE ALL RECORDS
# ============================================================

print("\n" + "=" * 70)
print("STARTING ALL FOCAL-METHOD TEST EXECUTIONS")
print("=" * 70)

input_records = records_df.to_dict(
    orient="records"
)

execution_results = []

with ThreadPoolExecutor(
    max_workers=MAX_WORKERS
) as executor:

    futures = [
        executor.submit(
            execute_record,
            record
        )
        for record in input_records
    ]

    for completed, future in enumerate(
        as_completed(futures),
        start=1,
    ):

        try:

            execution_results.append(
                future.result()
            )

        except Exception as exc:

            execution_results.append(
                {
                    "test_status":
                        "FUTURE_ERROR",

                    "coverage_status":
                        "FUTURE_ERROR",

                    "coverage_error":
                        str(exc),
                }
            )

        if (
            completed % 10 == 0
            or completed == len(futures)
        ):

            print(
                f"Completed "
                f"{completed}/"
                f"{len(futures)}",
                flush=True,
            )

per_repeat_df = pd.DataFrame(
    execution_results
)

print("\nExecution finished.")
print(
    "Per-repeat rows:",
    len(per_repeat_df)
)


# ============================================================
# 14. AGGREGATION WITHOUT NULL GROUPBY PROBLEMS
# ============================================================

GROUP_KEYS = [
    "provider",
    "model",
    "difficulty",
    "project_id",
    "bug_id",
    "task_id",
    "qualified_class_name",
    "function_name",
]

numeric_metrics = [
    "tests_found",
    "tests_started",
    "tests_successful",
    "tests_failed",
    "tests_aborted",
    "tests_skipped",
    "executability_rate_percent",
    "test_pass_rate_percent",
    "method_coverage_percent",
    "line_coverage_percent",
    "branch_coverage_percent",
    "instruction_coverage_percent",
    "focal_line_total",
    "focal_line_covered",
    "focal_branch_total",
    "focal_branch_covered",
    "evaluation_time_seconds",
]

def safe_mean(series):
    values = pd.to_numeric(
        series,
        errors="coerce"
    ).dropna()

    return (
        float(values.mean())
        if len(values)
        else None
    )

def safe_median(series):
    values = pd.to_numeric(
        series,
        errors="coerce"
    ).dropna()

    return (
        float(values.median())
        if len(values)
        else None
    )

def safe_std(series):
    values = pd.to_numeric(
        series,
        errors="coerce"
    ).dropna()

    return (
        float(values.std(ddof=0))
        if len(values)
        else None
    )

def safe_min(series):
    values = pd.to_numeric(
        series,
        errors="coerce"
    ).dropna()

    return (
        float(values.min())
        if len(values)
        else None
    )

def safe_max(series):
    values = pd.to_numeric(
        series,
        errors="coerce"
    ).dropna()

    return (
        float(values.max())
        if len(values)
        else None
    )

group_map = {}

for idx, row in per_repeat_df.iterrows():

    key = tuple(
        None
        if pd.isna(
            row.get(column)
        )
        else str(
            row.get(column)
        ).strip()
        for column in GROUP_KEYS
    )

    group_map.setdefault(
        key,
        []
    ).append(idx)

aggregated_rows = []

for key, indices in group_map.items():

    group = per_repeat_df.loc[
        indices
    ].copy()

    aggregated = {
        GROUP_KEYS[i]:
            key[i]
        for i in range(
            len(GROUP_KEYS)
        )
    }

    aggregated[
        "repeats_expected"
    ] = EXPECTED_REPEATS

    aggregated[
        "repeats_completed"
    ] = len(group)

    aggregated[
        "compile_ok_repeats"
    ] = int(
        (
            group[
                "compile_status"
            ]
            == "COMPILE_OK"
        ).sum()
    )

    aggregated[
        "compile_failed_repeats"
    ] = int(
        (
            group[
                "compile_status"
            ]
            == "COMPILE_FAILED"
        ).sum()
    )

    aggregated[
        "pass_repeats"
    ] = int(
        (
            group[
                "test_status"
            ]
            == "PASS"
        ).sum()
    )

    aggregated[
        "failed_repeats"
    ] = int(
        (
            group[
                "test_status"
            ]
            == "FAIL"
        ).sum()
    )

    aggregated[
        "timeout_repeats"
    ] = int(
        (
            group[
                "test_status"
            ]
            == "TIMEOUT"
        ).sum()
    )

    aggregated[
        "coverage_ok_repeats"
    ] = int(
        (
            group[
                "coverage_status"
            ]
            == "OK"
        ).sum()
    )

    aggregated[
        "coverage_status_summary"
    ] = "; ".join(
        f"{status}:{count}"
        for status, count
        in group[
            "coverage_status"
        ]
        .fillna("MISSING")
        .value_counts()
        .items()
    )

    for metric in numeric_metrics:

        aggregated[
            f"avg_{metric}"
        ] = safe_mean(
            group[metric]
        )

        aggregated[
            f"median_{metric}"
        ] = safe_median(
            group[metric]
        )

        aggregated[
            f"std_{metric}"
        ] = safe_std(
            group[metric]
        )

        aggregated[
            f"min_{metric}"
        ] = safe_min(
            group[metric]
        )

        aggregated[
            f"max_{metric}"
        ] = safe_max(
            group[metric]
        )

    # Mutation columns intentionally blank.
    aggregated[
        "avg_mutation_score_percent"
    ] = pd.NA

    aggregated[
        "avg_mutants_killed"
    ] = pd.NA

    aggregated[
        "avg_mutants_survived"
    ] = pd.NA

    aggregated[
        "avg_mutants_total"
    ] = pd.NA

    aggregated_rows.append(
        aggregated
    )

aggregated_df = pd.DataFrame(
    aggregated_rows
)


# ============================================================
# 15. COVERAGE STATUS + ERRORS
# ============================================================

coverage_status_df = (
    per_repeat_df[
        "coverage_status"
    ]
    .fillna("MISSING")
    .value_counts()
    .rename_axis(
        "coverage_status"
    )
    .reset_index(
        name="count"
    )
)

error_statuses = {
    "EVALUATOR_ERROR",
    "FUTURE_ERROR",
    "SOURCE_FILE_NOT_FOUND",
    "FOCAL_METHOD_NOT_FOUND",
    "FOCAL_CLASS_NOT_IN_JACOCO",
    "FOCAL_METHOD_NOT_IN_JACOCO",
    "FOCAL_METHOD_HAS_NO_COUNTERS",
    "NO_FOCAL_LINES_IN_JACOCO",
    "JACOCO_REPORT_FAILED",
    "NO_EXEC_FILE",
    "TIMEOUT",
}

errors_df = per_repeat_df[
    per_repeat_df[
        "coverage_status"
    ].isin(
        error_statuses
    )
].copy()


# ============================================================
# 16. FILE COVERAGE AUDIT
# ============================================================

file_result_summary = (
    per_repeat_df
    .groupby(
        [
            "provider",
            "model",
            "source_file",
            "source_path",
        ],
        dropna=False,
        sort=False
    )
    .agg(
        records=(
            "function_name",
            "size"
        ),

        coverage_ok=(
            "coverage_status",
            lambda s:
                int(
                    (
                        s == "OK"
                    ).sum()
                )
        ),

        compile_failed=(
            "compile_status",
            lambda s:
                int(
                    (
                        s == "COMPILE_FAILED"
                    ).sum()
                )
        ),

        passed=(
            "test_status",
            lambda s:
                int(
                    (
                        s == "PASS"
                    ).sum()
                )
        ),
    )
    .reset_index()
)

# ============================================================
# 17. METRIC DEFINITIONS + RUN CONFIG
# ============================================================

metric_definitions_df = pd.DataFrame(
    [
        [
            "Method Coverage",
            "Coverage of the selected focal method based on its JaCoCo METHOD counter.",
            "0–100%",
        ],
        [
            "Focal Line Coverage",
            "Coverage of the selected focal method from its JaCoCo LINE counter.",
            "0–100%",
        ],
        [
            "Focal Branch Coverage",
            "Coverage of the selected focal method from its JaCoCo BRANCH counter. Blank when the method has no branches.",
            "0–100% / N/A",
        ],
        [
            "Instruction Coverage",
            "Coverage of bytecode instructions for the selected focal method.",
            "0–100%",
        ],
        [
            "Test Pass Rate",
            "Successful JUnit tests divided by tests started.",
            "Percentage",
        ],
        [
            "Executability Rate",
            "JUnit tests started divided by tests found.",
            "Percentage",
        ],
        [
            "Coverage Scope",
            "Only the focal method is measured. Other project methods do not enter its coverage denominator.",
            "Focal method only",
        ],
        [
            "Execution Environment",
            "Full Defects4J checkout is used only to compile and execute the generated test.",
            "Project",
        ],
        [
            "Mutation Testing",
            "Not executed in this experiment.",
            "NOT RUN",
        ],
        [
            "Generated-test modification",
            "Generated test logic is not repaired. A package/class harness may be added only when the supplied test is a snippet without an enclosing class.",
            "No logic repair",
        ],
    ],
    columns=[
        "metric",
        "definition",
        "unit_or_scope",
    ],
)

run_config_df = pd.DataFrame(
    [
        [
            "execution_version",
            EXECUTION_VERSION
        ],
        [
            "expected_repeats",
            EXPECTED_REPEATS
        ],
        [
            "max_workers",
            MAX_WORKERS
        ],
        [
            "coverage_scope",
            "FOCAL METHOD ONLY"
        ],
        [
            "mutation_testing",
            "NOT RUN"
        ],
        [
            "jacoco_version",
            JACOCO_VERSION
        ],
        [
            "junit_console_version",
            JUNIT_VERSION
        ],
        [
            "defects4j",
            "Full checkout + compile"
        ],
    ],
    columns=[
        "setting",
        "value",
    ],
)


# ============================================================
# 18. INPUT AUDIT
# ============================================================

input_audit_df = pd.DataFrame(
    file_audit_rows
)

# ============================================================
# 19. FINAL EXCEL EXPORT
# ============================================================

print("\n" + "=" * 70)
print("CREATING FINAL EXCEL")
print("=" * 70)

OUTPUT_XLSX.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with pd.ExcelWriter(
    OUTPUT_XLSX,
    engine="xlsxwriter",
) as writer:

    aggregated_df.to_excel(
        writer,
        sheet_name="Function_Aggregated",
        index=False,
    )

    per_repeat_df.to_excel(
        writer,
        sheet_name="Per_Repeat",
        index=False,
    )

    input_audit_df.to_excel(
        writer,
        sheet_name="Input_File_Audit",
        index=False,
    )

    file_result_summary.to_excel(
        writer,
        sheet_name="File_Result_Summary",
        index=False,
    )

    coverage_status_df.to_excel(
        writer,
        sheet_name="Coverage_Status",
        index=False,
    )

    errors_df.to_excel(
        writer,
        sheet_name="Errors",
        index=False,
    )

    run_config_df.to_excel(
        writer,
        sheet_name="Run_Config",
        index=False,
    )

    metric_definitions_df.to_excel(
        writer,
        sheet_name="Metric_Definitions",
        index=False,
    )

# ============================================================
# 20. VERIFY + DOWNLOAD
# ============================================================

if not OUTPUT_XLSX.exists():

    raise FileNotFoundError(
        f"Excel file was not created: "
        f"{OUTPUT_XLSX}"
    )

excel_check = pd.ExcelFile(
    OUTPUT_XLSX
)

expected_sheets = {
    "Function_Aggregated",
    "Per_Repeat",
    "Input_File_Audit",
    "File_Result_Summary",
    "Coverage_Status",
    "Errors",
    "Run_Config",
    "Metric_Definitions",
}

missing_sheets = (
    expected_sheets
    - set(
        excel_check.sheet_names
    )
)

excel_check.close()

if missing_sheets:

    raise RuntimeError(
        "Missing Excel sheets: "
        + str(
            sorted(
                missing_sheets
            )
        )
    )

print("=" * 70)
print("FINAL EVALUATION SUMMARY")
print("=" * 70)

print(
    "Input dataset:",
    DATASET_JSON.name
)

print(
    "Records extracted:",
    len(records_df)
)

print(
    "Focal targets:",
    len(
        aggregated_df
    )
)

print(
    "Repeat evaluations:",
    len(
        per_repeat_df
    )
)

print(
    "Coverage OK:",
    int(
        (
            per_repeat_df[
                "coverage_status"
            ]
            == "OK"
        ).sum()
    )
)

print(
    "Coverage missing/error:",
    int(
        (
            per_repeat_df[
                "coverage_status"
            ]
            != "OK"
        ).sum()
    )
)

print(
    "Mutation testing: NOT RUN"
)

print(
    "Output:",
    OUTPUT_XLSX
)

print(
    "Size:",
    f"{OUTPUT_XLSX.stat().st_size / (1024**2):.2f} MB"
)

print("\nCoverage counts:")
for column in [
    "method_coverage_percent",
    "line_coverage_percent",
    "branch_coverage_percent",
    "instruction_coverage_percent",
]:

    count = (
        pd.to_numeric(
            per_repeat_df[column],
            errors="coerce",
        )
        .notna()
        .sum()
    )

    print(
        f" - {column}: "
        f"{count}/{len(per_repeat_df)}"
    )

print("\n✓ Workbook verified.")

from google.colab import files

files.download(
    str(OUTPUT_XLSX)
)

print("✓ Download started.")