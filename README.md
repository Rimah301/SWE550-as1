SQAI Replication Package
This repository provides the research artefacts and reproduction
resources for the study "Empirical Evaluation of Semantic Quality in
LLM-Generated Unit Tests Using the SQAI Framework."
The package supports the study of semantic quality in LLM-generated unit
tests for Python functions and Java methods. The source datasets cover
30 Python items and 30 Java items across three operational difficulty
levels: Easy, Medium, and Hard (10 items per level).
Repository structure
``` text
SWE550-as1/
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
├── Reproduction/
│   ├── .env.example
│   ├── CONFIGURATION.md
│   ├── requirements.txt
│   ├── Prompts/
│   ├── Scripts/
│   ├── Notebooks/
│   └── Configurations/
└── Statistical_Analysis/
    ├── inputs/
    │   ├── manual/
    │   └── automatic/
    ├── scripts/
    │   └── run_all.py
    └── outputs/
        ├── Python_Java_Execution_Coverage_Paper_Aligned.json
        └── Java_Execution_Coverage_Updated_Gemini_Hard.json
```
> Keep this tree synchronized with the files actually committed to the
> repository. The `Statistical_Analysis/` folder and the listed output
> filenames should appear here only after they have been uploaded.
Study data
Source datasets
The `Datasets/` directory contains the Python and Java source datasets
used in the study, including the selected program items and the
reference information supplied with the study.
Generated unit tests
The `Generated_Tests/` directory contains the generated unit-test
artefacts for Python and Java.
The presence of a generated test in these files does not, by itself,
establish that the test compiled, executed, or passed. Generation,
execution status, and coverage are treated as separate outcomes.
Manual SQAI evaluation
The `Manual_Evaluation/` directory contains the manual evaluation
results and the scoring rubric for Code-Based SQAI.
Q1--Q9 represent criterion-level questions, while Q10 provides a
separate holistic judgement. Not-applicable responses are excluded from
numerical aggregation where specified by the scoring protocol.
Statistical analysis
The `Statistical_Analysis/` directory contains the scripts, input
workbooks, and machine-readable outputs used for quantitative analysis.
`inputs/manual/`: manual SQAI evaluation workbooks.
`inputs/automatic/`: execution, coverage, and other automatically
collected experimental results.
`scripts/`: scripts used to process inputs and calculate the
reported statistics.
`outputs/`: consolidated results in machine-readable formats.
Python and Java results are retained separately where their measures or
baselines differ. The analysis includes descriptive SQAI summaries and
execution-based measures, as well as statistical comparisons implemented
by the analysis scripts.
Coverage data notes
Coverage summaries should be interpreted alongside execution
denominators and measurement status. A test-generation record is not
equivalent to successful execution, and a passing test is not equivalent
to a test that provides meaningful coverage.
The updated Gemini Java Hard coverage output includes function-level,
project-level, and execution-status summaries. Its overall means are
based on the ten function-level summaries in the supplied results
workbook. The zero-coverage results for the three Math methods require
validation of method invocation, instrumentation, and method matching
before substantive conclusions are drawn.
The combined line-and-branch coverage value is a study-defined summary
measure; it is not a standard standalone JaCoCo metric.
Reproducing the experiments
The source datasets are provided in JSON format. The experiment runners
use Excel input files. Create the required workbooks first:
``` bash
python Reproduction/Scripts/prepare_datasets.py --output-dir /content
```
Install the Python dependencies:
``` bash
pip install -r Reproduction/requirements.txt
```
For LLM-based generation, copy the example environment file, configure
the selected model, and provide the required provider API key through an
environment variable:
``` bash
cp Reproduction/.env.example Reproduction/.env
python Reproduction/Scripts/run_llm_generation.py
```
For the Pynguin experiment:
``` bash
python Reproduction/Scripts/pynguin_colab_experiment.py
```
For Java generation with EvoSuite and Defects4J, open
`Reproduction/Notebooks/EvoSuite_Defects4J_Experiment.ipynb` in Google
Colab and follow the notebook instructions.
For detailed setup instructions, experiment parameters, input
requirements, and output descriptions, see
Reproduction/CONFIGURATION.md.
After uploading the statistical-analysis package and its required input
files, run the analysis from the repository root:
``` bash
python Statistical_Analysis/scripts/run_all.py
```
Check the analysis script documentation for the exact expected filenames
and options.
Reproducibility considerations
The scripts and configuration files document the experimental settings
available in the supplied research artefacts. Historical environment
details should be reported only when supported by the recorded
experiment configuration.
Execution-based measures should be interpreted separately from
test-generation counts. Generated tests may fail to compile, fail during
execution, or execute without passing. Coverage values also depend on
successful instrumentation and correct mapping between tests and target
methods.
Credentials and responsible use
Do not commit API keys, access tokens, or private credentials. Keep the
local `Reproduction/.env` file out of version control and use
`Reproduction/.env.example` only as a configuration template.
The Pynguin workflow imports and executes source modules during
generation. Run scripts only on source code you trust and in an
appropriately isolated environment.
Repository
https://github.com/Rimah301/SWE550-as1
License
No licence terms were supplied with the source materials. Add the
licence selected by the authors before redistributing this repository
publicly.
