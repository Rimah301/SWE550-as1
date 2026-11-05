# SQAI Replication Package

This repository contains the datasets, generated unit-test artifacts, manual SQAI evaluations, scoring rubric, prompts, and reproduction scripts used in the study of semantic quality in LLM-generated unit tests.

The study considers Python functions and Java methods across Easy, Medium, and Hard difficulty levels. The supplied source datasets contain 30 Python items and 30 Java items, with 10 items in each difficulty level.

## Repository contents

```text
SQAI_Replication_Package/
├── README.md
├── Datasets/
│   ├── Python_Dataset.json
│   └── Java_Dataset.json
├── Generated_Tests/
│   ├── Python_Generated_Tests.json
│   └── Java_Generated_Tests.json
├── Manual_Evaluation/
│   ├── SQAI_Evaluation_Results.xlsx
│   └── SQAI_Scoring_Rubric.xlsx
└── Reproduction/
    ├── .env.example
    ├── CONFIGURATION.md
    ├── requirements.txt
    ├── Prompts/
    │   └── prompts.txt
    ├── Scripts/
    │   ├── prepare_datasets.py
    │   ├── model_agnostic_test_generation.py
    │   ├── run_llm_generation.py
    │   └── pynguin_colab_experiment.py
    ├── Notebooks/
    │   └── EvoSuite_Defects4J_Experiment.ipynb
    └── Configurations/
```

## Quick start

The source datasets are provided in JSON format. The experiment runners use Excel input files. Create the required workbooks first:

```bash
python Reproduction/Scripts/prepare_datasets.py --output-dir /content
```

Install the Python packages:

```bash
pip install -r Reproduction/requirements.txt
```

For LLM-based generation, copy `Reproduction/.env.example` to `Reproduction/.env`, set `LLM_MODEL`, and provide the API key required by the selected provider as an environment variable. Then run:

```bash
python Reproduction/Scripts/run_llm_generation.py
```

For the Pynguin experiment, run:

```bash
python Reproduction/Scripts/pynguin_colab_experiment.py
```

For Java generation with EvoSuite and Defects4J, open `Reproduction/Notebooks/EvoSuite_Defects4J_Experiment.ipynb` in Google Colab and follow the instructions in the notebook.

Detailed setup instructions, experiment parameters, input requirements, and output descriptions are provided in [Reproduction/CONFIGURATION.md](Reproduction/CONFIGURATION.md).

## Data and artifact notes

- The JSON datasets preserve the source code and reference tests supplied with the study.
- The generated-test JSON files are included as supplied. They contain 180 Python records and 181 Java records.
- The manual evaluation workbook contains separate Java and Python evaluation sheets.
- The scoring workbook documents the Code-Based SQAI scoring method and rubric.
- Statistical analysis files are not part of this release and will be added separately.

## Reproduction considerations

The scripts preserve the experimental settings recorded in the supplied source files. Some historical environment details were not available in those files, including the exact Pynguin package version and complete provider-side settings for all LLM calls. The configuration guide identifies these limitations rather than assigning unverified values.

Generated test files should not be interpreted as evidence that tests compiled or executed successfully. The EvoSuite notebook explicitly treats generation success as the presence of at least one generated `*_ESTest.java` file. Execution-based measures should be considered separately.

## Credentials and responsible use

Do not commit API keys, access tokens, or private credentials. Keep the local `Reproduction/.env` file out of version control. Pynguin imports and executes the source module during generation; only run the script on source code you trust.

## License

No licence terms were supplied with the source materials. Add the licence selected by the authors before redistributing this repository publicly.
