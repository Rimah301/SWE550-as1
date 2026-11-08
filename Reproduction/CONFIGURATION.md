# Reproduction configuration

This document records the settings visible in the supplied experiment code and explains how to reproduce the statistical analysis. It distinguishes settings explicitly encoded in the artefacts from details that were not preserved.

## 1. Prepare the input workbooks

The repository stores the source datasets as JSON. The generation scripts and the EvoSuite notebook expect Excel workbooks, so create those workbooks from the JSON files before running a new experiment:

```bash
python Reproduction/Scripts/prepare_datasets.py --output-dir /content
```

The script creates the following files:

| Dataset | Output workbook |
|---|---|
| Python / Easy | `easy_python_dataset.xlsx` |
| Python / Medium | `midum_python_dataset.xlsx` |
| Python / Hard | `Hard_python_dataset.xlsx` |
| Java / Easy | `Java_easy_dataset_cleaned.xlsx` |
| Java / Medium | `Java_midum_dataset.xlsx` |
| Java / Hard | `Java_Hard_dataset_cleaned.xlsx` |

## 2. LLM-based test generation

The model-agnostic runner is `Reproduction/Scripts/model_agnostic_test_generation.py`. `Reproduction/Scripts/run_llm_generation.py` supplies the model-call adapter through LiteLLM.

1. Install dependencies:
   ```bash
   pip install -r Reproduction/requirements.txt
   ```
2. Set `LLM_MODEL` to a LiteLLM-supported provider/model identifier.
3. Set the corresponding provider API key as an environment variable. Do not commit credentials.
4. Prepare the six Excel workbooks as described in Section 1.
5. Run:
   ```bash
   python Reproduction/Scripts/run_llm_generation.py
   ```

### Generation protocol encoded in the runner

- Python uses pytest; Java uses JUnit.
- Each source item receives five separate requests.
- Each request uses a newly constructed prompt and does not include earlier conversation turns or earlier generated responses.
- Each request asks for exactly five test cases.
- Checkpoints are appended after each repetition, and workbooks are updated during processing.
- A completed repetition is not submitted again when its checkpoint record is present.

The adapter makes one stateless provider request per prompt. It does not set a temperature or token limit; provider defaults may apply. Input, output, and checkpoint directories default to `/content` locations. Set `SQAI_INPUT_DIR`, `SQAI_OUTPUT_DIR`, and `SQAI_CHECKPOINT_DIR` if different locations are required.

The supplied generated-test JSON files are retained as research artefacts and are not regenerated during repository setup. They contain 180 Python records and 181 Java records.

## 3. Pynguin (Python search-based generation)

Run `Reproduction/Scripts/pynguin_colab_experiment.py` after preparing the three Python workbooks:

```bash
python Reproduction/Scripts/pynguin_colab_experiment.py
```

Recorded settings:

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

The exact historical Pynguin package version was not preserved. The script records the installed version at run time.

**Execution caution:** Pynguin imports and executes the supplied source module during generation. Run it only on trusted source code and in an appropriately isolated environment.

## 4. EvoSuite with Defects4J (Java search-based generation)

Open `Reproduction/Notebooks/EvoSuite_Defects4J_Experiment.ipynb` in Google Colab and run its cells from top to bottom after preparing the Java workbooks. The notebook setup expects Java 11, Maven, Subversion, Git, Perl, and the required Perl modules. It locates EvoSuite 1.1.0 within the Defects4J installation.

Recorded settings:

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

A run is marked successful when at least one `*_ESTest.java` file is generated. This indicates test generation only; it does not establish that the generated tests compile or execute successfully.

## 5. Reproduce the statistical analysis

The statistical input files are included in `Statistics/Inputs/`:

- `Execution_and_Coverage_Results(1).json`
- `Python_Unit_Test_Evaluations_Revised_Strict_Aligned(1)(20261108-105627).xlsx`
- `Java_Unit_Test_Evaluations_Corrected(4).xlsx`

The two evaluation workbooks provide an `All Evaluations` worksheet. The reproduction script reads these worksheets and computes SQAI summaries, matched-target comparisons, Friedman tests with Kendall's W, Wilcoxon signed-rank comparisons with Holm adjustment, and coverage validation tables.

From the repository root, install dependencies:

```bash
pip install -r Reproduction/requirements.txt
```

Then run:

```bash
python Reproduction/Scripts/Reproduce_Statistics.py \
  --coverage-json "Statistics/Inputs/Execution_and_Coverage_Results(1).json" \
  --python-xlsx "Statistics/Inputs/Python_Unit_Test_Evaluations_Revised_Strict_Aligned(1)(20261108-105627).xlsx" \
  --java-xlsx "Statistics/Inputs/Java_Unit_Test_Evaluations_Corrected(4).xlsx" \
  --output "Statistics/SQAI_Reproduced_Statistics.xlsx"
```

The script writes the reproduced workbook and its validation report under `Statistics/`. The repository also includes the current statistical results workbook, `Statistics/SQAI_Statistics_Results.xlsx`, and validation record, `Statistics/SQAI_Statistics_Validation.txt`.

## 6. Scope and reproducibility notes

- Python pass rate and Java measurement rate are different execution indicators and must not be directly compared as if they were the same metric.
- Null/N/A coverage values are not zero. A target without a valid coverage measurement is excluded from the conditional coverage mean and reflected in the measurement rate.
- The overall cross-difficulty coverage average is not substituted for difficulty-specific values reported in the paper.
- The supplied artefacts do not preserve a verified historical Pynguin package version or complete provider-side model parameters for every LLM call. These details are not inferred or fabricated.
- Model API identifiers, provider availability, and service-side model versions can change. For exact historical reproduction, use the same model snapshot and provider settings as the original experiment where those details are available.
- Generated-test source files are not equivalent to executed tests. Compilation, execution, coverage, mutation testing, and other evaluation stages should be reported separately.
