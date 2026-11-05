# Reproduction configuration

This document records the settings visible in the supplied experiment code. It separates settings that are explicitly encoded in the scripts from details that were not preserved in the uploaded artifacts.

## 1. Prepare the input workbooks

The repository stores the source datasets as JSON. The generation scripts and the EvoSuite notebook expect Excel workbooks, so create those workbooks from the JSON files before running an experiment:

```bash
python Reproduction/Scripts/prepare_datasets.py --output-dir /content
```

The script creates these files:

| Dataset | Output workbook |
|---|---|
| Python / Easy | `easy_python_dataset.xlsx` |
| Python / Medium | `midum_python_dataset.xlsx` |
| Python / Hard | `Hard_python_dataset.xlsx` |
| Java / Easy | `Java_easy_dataset_cleaned.xlsx` |
| Java / Medium | `Java_midum_dataset.xlsx` |
| Java / Hard | `Java_Hard_dataset_cleaned.xlsx` |

The workbooks retain the source-record fields and include the source-code columns expected by the runners. The Python source code is exposed as `original_code` as well as the original dataset field. Java records retain `buggy_code_from_dataset` and the Defects4J metadata.

## 2. LLM-based test generation

### Configuration

The runner is `Scripts/model_agnostic_test_generation.py`. It is model-agnostic and accepts a callable named `model_call(prompt)`. The companion script `Scripts/run_llm_generation.py` supplies that callable through LiteLLM.

1. Install the Python dependencies:
   ```bash
   pip install -r Reproduction/requirements.txt
   ```
2. Copy `Reproduction/.env.example` to `Reproduction/.env`.
3. Set `LLM_MODEL` to the provider/model identifier supported by LiteLLM.
4. Set the provider's API key as an environment variable. Do not put credentials in the repository.
5. Prepare the six Excel workbooks as described above.
6. Run:
   ```bash
   python Reproduction/Scripts/run_llm_generation.py
   ```

### Generation protocol encoded in the runner

- Python uses pytest; Java uses JUnit.
- Each source item receives five separate requests.
- Every request uses a newly constructed prompt and does not include earlier conversation turns or earlier generated responses.
- Each request asks for exactly five test cases.
- The prompts request deterministic, independent tests and ask the model to infer behaviour from the supplied source code and interface.
- Checkpoints are appended after each repetition, and workbooks are updated during processing.
- A completed repetition is not submitted again when its checkpoint record is present.

The adapter deliberately makes one stateless provider request for each prompt. It does not set a temperature or token limit because those values are not specified by the supplied experiment script. Provider defaults may therefore apply.

The run output and checkpoint directories are separated by model identifier under `/content`, so running another model does not overwrite the previous model's files. Set `SQAI_INPUT_DIR`, `SQAI_OUTPUT_DIR`, and `SQAI_CHECKPOINT_DIR` explicitly if a different location is required.

### Existing generated-test data

The supplied generated-test JSON files are retained as research artifacts. They are not regenerated when the repository is prepared. The current JSON files contain 180 Python records and 181 Java records; the Java file is retained as supplied rather than silently changing or discarding a record.

## 3. Pynguin (Python search-based generation)

Run `Scripts/pynguin_colab_experiment.py` after preparing the three Python workbooks:

```bash
pip install pynguin pandas openpyxl
python Reproduction/Scripts/pynguin_colab_experiment.py
```

The script's recorded settings are:

| Setting | Value |
|---|---|
| Target functions | 30 (10 per difficulty level) |
| Repetitions | 5 per function |
| Expected total runs | 150 |
| Search algorithm | `DYNAMOSA` |
| Element visibility | `PROTECTED` |
| Search-time budget | 300 seconds (soft limit) |
| External process timeout | 330 seconds |
| Repetition seeds | 1, 2, 3, 4, 5 |
| Statistics backend | `CSV` |

The script records the installed Pynguin version at run time. The exact version used in the historical experiment was not present in the preserved files, so no historical version is asserted here.

**Execution caution:** Pynguin imports and executes the supplied source module during generation. Run it only on source code you trust. The script requires the operator to type `TRUSTED` before continuing.

The output directory defaults to `/content/pynguin_experiment`. It contains checkpoints, generated test files, reports, process logs, an Excel workbook, and a ZIP archive.

## 4. EvoSuite with Defects4J (Java search-based generation)

Open `Notebooks/EvoSuite_Defects4J_Experiment.ipynb` in Google Colab and run its cells from top to bottom after preparing the Java workbooks.

The notebook installs or checks the system dependencies and prepares Defects4J. Its setup expects Java 11, Maven, Subversion, Git, Perl, and the required Perl modules. It locates EvoSuite 1.1.0 within the Defects4J installation.

The notebook's recorded experiment settings are:

| Setting | Value |
|---|---|
| Target methods | 30 (10 per difficulty level) |
| Repetitions | 5 per method |
| Expected total runs | 150 |
| EvoSuite search budget | 60 seconds per run |
| Maximum workers | 10 |
| Defects4J checkout timeout | 600 seconds |
| Compilation timeout | 1,200 seconds |
| Classpath timeout | 180 seconds |
| EvoSuite process timeout | 3,600 seconds |
| Retry failed runs | Disabled by default |
| Reset checkpoints | Disabled by default |

Each repetition receives its own Defects4J checkout and output directory. A run is marked successful when at least one `*_ESTest.java` file is generated. This status indicates generation only; it does not establish that the generated tests compile or execute successfully.

The notebook preserves checkpoints and exports workbooks, JSONL data, raw checkpoint files, and a ZIP archive. Do not enable checkpoint reset unless the existing run state is intentionally being discarded.

## 5. Scope and reproducibility notes

- The repository includes the source datasets, generated-test JSON files, manual SQAI evaluation workbook, scoring rubric, prompts, and experiment code supplied for this package.
- Statistical analysis files are intentionally not included in this release and can be added in a later update.
- The uploaded artifacts do not provide a verified historical package version for Pynguin or complete provider-side model parameters for every LLM call. These are not inferred or fabricated here.
- Model API identifiers, provider availability, and service-side model versions can change. For exact historical reproduction, use the same model snapshot and provider settings as the original experiment where those details are available.
- Generated-test source files are not equivalent to executed tests. Compilation, execution, coverage, mutation testing, and other evaluation stages should be reported separately.
