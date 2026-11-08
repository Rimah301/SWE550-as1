# SQAI Replication Package

This repository contains the research artefacts and reproduction resources for **“Empirical Evaluation of Semantic Quality in LLM-Generated Unit Tests Using the SQAI Framework.”** The study evaluates the semantic quality of LLM-generated unit tests for Python functions and Java methods.

The source datasets contain 30 Python items and 30 Java items across three operational difficulty levels: Easy, Medium, and Hard (10 items per level).

## Repository structure

```text
SWE550-as1/
├── README.md
├── Datasets/
│   ├── Python_Dataset.json
│   └── Java_Dataset.json
├── Generated_Tests/
│   ├── Python_Generated_Tests.json
│   └── Java_Generated_Tests.json
├── Execution and Coverage Results/
│   └── Execution_and_Coverage_Results.json
├── Manual_Evaluation/
│   ├── SQAI_Evaluation_Results.xlsx
│   └── SQAI_Scoring_Rubric.xlsx
├── Reproduction/
│   ├── CONFIGURATION.md
│   ├── requirements.txt
│   ├── Notebooks/
│   │   └── EvoSuite_Defects4J_Experiment.ipynb
│   ├── Prompts/
│   │   └── prompts.txt
│   └── Scripts/
│       ├── Reproduce_Statistics.py
│       ├── java_focal_method_coverage.py
│       ├── model_agnostic_test_generation.py
│       ├── prepare_datasets.py
│       ├── pynguin_colab_experiment.py
│       ├── python_focal_method_coverage.py
│       └── run_llm_generation.py
└── Statistics/
    ├── Inputs/
    │   ├── Execution_and_Coverage_Results(1).json
    │   ├── Java_Unit_Test_Evaluations_Corrected(4).xlsx
    │   └── Python_Unit_Test_Evaluations_Revised_Strict_Aligned(1)(20261108-105627).xlsx
    ├── SQAI_Statistics_Results.xlsx
    └── SQAI_Statistics_Validation.txt
```

## Study data

### Source datasets

`Datasets/` contains the Python and Java source datasets used in the study, including the selected program items and their reference information.

### Generated unit tests

`Generated_Tests/` contains the generated unit-test artefacts for Python and Java. A generated-test record does not, by itself, establish that a test compiled, executed, or passed. Generation, execution status, and coverage are distinct outcomes.

### Execution and coverage

`Execution and Coverage Results/` contains the machine-readable execution and coverage results. The paper-aligned input used for the statistical reproduction is separately preserved in `Statistics/Inputs/`. Python pass rate and Java measurement rate are different indicators and should not be compared as if they were the same metric. Null/N/A coverage values are not zero.

### Manual SQAI evaluation

`Manual_Evaluation/` contains the manual SQAI evaluation workbook and scoring rubric. Q1–Q9 are criterion-level questions; Q10 is a separate holistic judgement. Numerical aggregation follows the scoring protocol, including its treatment of N/A responses.

## Statistical analysis

`Statistics/Inputs/` contains the exact input workbooks and JSON file used by the reproduction script. `Statistics/SQAI_Statistics_Results.xlsx` contains the statistical result tables, and `Statistics/SQAI_Statistics_Validation.txt` records the validation checks.

To reproduce the analysis, install the dependencies and run the command documented in `Reproduction/CONFIGURATION.md`. The script writes a reproduced statistics workbook and a validation report to `Statistics/`.

## Reproducing the experiments

The source datasets are supplied in JSON format. The experiment runners use Excel input workbooks. Create those workbooks first:

```bash
python Reproduction/Scripts/prepare_datasets.py --output-dir /content
```

Install the Python dependencies:

```bash
pip install -r Reproduction/requirements.txt
```

For LLM-based generation, configure `LLM_MODEL` and the relevant provider API key as environment variables, then run:

```bash
python Reproduction/Scripts/run_llm_generation.py
```

For the Pynguin experiment:

```bash
python Reproduction/Scripts/pynguin_colab_experiment.py
```

For Java generation with EvoSuite and Defects4J, open `Reproduction/Notebooks/EvoSuite_Defects4J_Experiment.ipynb` in Google Colab and follow the notebook instructions.

See `Reproduction/CONFIGURATION.md` for setup details, experiment parameters, input requirements, and statistical reproduction instructions.

## Reproducibility considerations

The scripts and configuration files document the experimental settings available in the supplied artefacts. Historical environment details should be reported only when supported by the recorded experiment configuration. Generated-test source files are not equivalent to executed tests. Compilation, execution, coverage, mutation testing, and other evaluation stages should be reported separately.

## Credentials and responsible use

Do not commit API keys, access tokens, or private credentials. Store credentials locally as environment variables or in a local `.env` file excluded from version control. The Pynguin workflow imports and executes source modules during generation; run it only on source code you trust and in an appropriately isolated environment.

## Repository

https://github.com/Rimah301/SWE550-as1

## License

No licence terms were supplied with the source materials. Add the licence selected by the authors before redistributing this repository publicly.
