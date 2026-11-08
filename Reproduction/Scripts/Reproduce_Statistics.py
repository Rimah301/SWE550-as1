#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reproduce and audit the SQAI statistics for the supplied Python/Java workbooks
and the paper-aligned execution/coverage JSON.

Install:
    pip install pandas numpy scipy openpyxl

Run:
    python reproduce_sqai_statistics.py \
      --coverage-json "Execution_and_Coverage_Results(1).json" \
      --python-xlsx "Python_Unit_Test_Evaluations_Revised_Strict_Aligned(1)(20261108-105627).xlsx" \
      --java-xlsx "Java_Unit_Test_Evaluations_Corrected(4).xlsx"

Outputs:
    SQAI_Reproduced_Statistics.xlsx
    SQAI_Reproduced_Statistics_validation.txt

Important:
- SQAI composites are calculated as the mean of numerically applicable Q1-Q10
  scores at each target-generator row; N/A values are excluded.
- The inferential unit is the target, not the five repeated LLM runs.
- A target enters paired comparisons only when all six generator composites
  are assessable. This should yield 28 matched targets (10 Easy, 9 Medium,
  9 Hard) in each language.
- Python pass rate and Java measurement rate are different metrics.
- Coverage values are checked against the exact values printed in Table IV.
  The JSON is not used to invent or recalculate coverage observations.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import friedmanchisquare, wilcoxon


# Published Table IV(a): pass rate, line coverage, branch coverage, reports.
PAPER_PYTHON = {
    ("Claude Fable 5.1", "Easy"):   (100.0, 50.2, None, 50),
    ("Claude Fable 5.1", "Medium"): (100.0, 64.9, 100.0, 50),
    ("Claude Fable 5.1", "Hard"):   (100.0, 75.1, 66.8, 50),
    ("DeepSeek-V4.1", "Easy"):     (86.0, 81.2, 60.0, 50),
    ("DeepSeek-V4.1", "Medium"):   (46.5, 48.7, 34.2, 43),
    ("DeepSeek-V4.1", "Hard"):     (55.6, 36.4, 22.0, 45),
    ("GPT-5.6", "Easy"):           (68.0, 85.7, 75.0, 50),
    ("GPT-5.6", "Medium"):         (39.1, 69.3, 61.4, 46),
    ("GPT-5.6", "Hard"):           (96.0, 56.8, 41.1, 50),
    ("Gemini 3.8", "Easy"):        (90.0, 91.7, 100.0, 50),
    ("Gemini 3.8", "Medium"):      (50.0, 73.9, 68.5, 46),
    ("Gemini 3.8", "Hard"):        (82.0, 60.0, 44.6, 50),
    ("Pynguin", "Easy"):           (91.0, 93.3, 48.1, 73),
    ("Pynguin", "Medium"):         (33.7, 64.6, 60.9, 80),
    ("Pynguin", "Hard"):           (79.8, 10.8, 5.7, 21),
    ("Qwen 3.8", "Easy"):          (18.9, 33.3, 0.0, 1),
    ("Qwen 3.8", "Medium"):        (15.6, 33.1, 27.1, 2),
    ("Qwen 3.8", "Hard"):          (5.0, None, None, 0),
}

# Published Table IV(b): mean line coverage, measured targets, denominator.
PAPER_JAVA = {
    ("Claude Fable 5.1", "Easy"):   (76.2, 9, 10),
    ("Claude Fable 5.1", "Medium"): (74.6, 10, 10),
    ("Claude Fable 5.1", "Hard"):   (73.8, 10, 10),
    ("DeepSeek", "Easy"):           (100.0, 8, 10),
    ("DeepSeek", "Medium"):         (72.5, 9, 10),
    ("DeepSeek", "Hard"):           (59.8, 2, 8),
    ("Gemini", "Easy"):             (100.0, 8, 10),
    ("Gemini", "Medium"):           (78.4, 10, 10),
    ("Gemini", "Hard"):             (26.5, 10, 10),
    ("GPT", "Easy"):                (76.2, 9, 10),
    ("GPT", "Medium"):              (73.2, 8, 10),
    ("GPT", "Hard"):                (54.4, 6, 10),
    ("Qwen 3.8", "Easy"):           (100.0, 1, 10),
    ("Qwen 3.8", "Medium"):         (81.4, 2, 10),
    ("Qwen 3.8", "Hard"):           (64.3, 2, 10),
    ("EvoSuite", "Easy"):           (52.6, 4, 10),
    ("EvoSuite", "Medium"):         (60.3, 6, 10),
    ("EvoSuite", "Hard"):           (9.5, 1, 10),
}

# Published pooled LLM criterion means, expressed as percentages of the 0-4 scale.
PAPER_SQAI = {
    "Python": {
        1: (83.7, 148), 2: (97.8, 148), 4: (97.1, 148),
        5: (83.7, 148), 6: (91.4, 29), 7: (84.4, 148),
        9: (84.4, 148), 10: (84.7, 148),
    },
    "Java": {
        1: (83.6, 148), 2: (92.5, 148), 4: (91.4, 148),
        5: (80.6, 145), 6: (70.4, 50), 7: (80.4, 148),
        10: (83.5, 148),
    },
}
PAPER_COMPOSITE = {
    "Python": (87.96, 34.36),
    "Java": (85.28, 90.63),
}


def close(a, b, tol=1e-8):
    if a is None or pd.isna(a):
        return b is None
    if b is None:
        return False
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


def holm_adjust(p_values):
    """Holm step-down adjusted p-values, preserving original order."""
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adjusted = np.empty(n, dtype=float)
    running = 0.0
    for rank, idx in enumerate(order):
        candidate = (n - rank) * p[idx]
        running = max(running, candidate)
        adjusted[idx] = min(running, 1.0)
    return adjusted


def load_scores(path, language):
    df = pd.read_excel(path, sheet_name="All Evaluations")
    if language == "Python":
        score_cols = [f"Question {i} - Score" for i in range(1, 11)]
        required = {"Model", "Level", "Project", *score_cols}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Python workbook missing columns: {sorted(missing)}")
        df["Target"] = df["Project"].astype(str)
        aliases = {
            "Claude": "Claude", "DeepSeek": "DeepSeek", "GPT": "GPT",
            "Gemini": "Gemini",
            "Qwen2.5-Coder-7B-Instruct": "Qwen",
            "Pynguin": "Baseline",
        }
    else:
        score_cols = [
            "Q1 Expected Output Correctness",
            "Q2 Assertion Meaningfulness",
            "Q3 Behaviour Verification",
            "Q4 Representative Inputs",
            "Q5 Boundary Behaviour",
            "Q6 Exceptional Behaviour",
            "Q7 Scenario Completeness",
            "Q8 Logical Consistency",
            "Q9 Evidence Support",
            "Q10 Overall Semantic Quality",
        ]
        required = {"Model", "Level", "Task ID", *score_cols}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Java workbook missing columns: {sorted(missing)}")
        df["Target"] = df["Task ID"].astype(str)
        aliases = {
            "Claude": "Claude", "DeepSeek": "DeepSeek", "GPT": "GPT",
            "Gemini": "Gemini", "Qwen": "Qwen", "EvoSuite": "Baseline",
        }

    df["Generator"] = df["Model"].map(aliases)
    if df["Generator"].isna().any():
        unknown = sorted(df.loc[df["Generator"].isna(), "Model"].astype(str).unique())
        raise ValueError(f"Unmapped generator names in {language}: {unknown}")

    for col in score_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Mean of all numerically applicable Q1-Q10; Q10 is intentionally included.
    df["N_numeric_Q1_Q10"] = df[score_cols].notna().sum(axis=1)
    df["Composite_0_4"] = df[score_cols].mean(axis=1, skipna=True)
    df.loc[df["N_numeric_Q1_Q10"] == 0, "Composite_0_4"] = np.nan
    df["Composite_percent"] = df["Composite_0_4"] / 4.0 * 100.0
    return df, score_cols


def criterion_tables(df, score_cols, language):
    llm_names = ["Claude", "DeepSeek", "GPT", "Gemini", "Qwen"]
    llm = df[df["Generator"].isin(llm_names)].copy()
    rows = []
    for qnum, col in enumerate(score_cols, start=1):
        vals = llm[col].dropna()
        rows.append({
            "Language": language, "Question": f"Q{qnum}",
            "N": int(vals.count()),
            "Mean_0_4": vals.mean() if len(vals) else np.nan,
            "Mean_percent_of_4": vals.mean() / 4 * 100 if len(vals) else np.nan,
            "SD_0_4": vals.std(ddof=1) if len(vals) > 1 else np.nan,
            "Median_0_4": vals.median() if len(vals) else np.nan,
        })
    pooled = pd.DataFrame(rows)

    by_level_rows = []
    for level in ["Easy", "Medium", "Hard"]:
        sub = llm[llm["Level"].astype(str).str.title() == level]
        for qnum, col in enumerate(score_cols, start=1):
            vals = sub[col].dropna()
            by_level_rows.append({
                "Language": language, "Level": level, "Question": f"Q{qnum}",
                "N": int(vals.count()),
                "Mean_0_4": vals.mean() if len(vals) else np.nan,
                "Mean_percent_of_4": vals.mean() / 4 * 100 if len(vals) else np.nan,
            })
    return pooled, pd.DataFrame(by_level_rows)


def matched_composites(df, language):
    # One row per focal target x generator. The source workbooks contain
    # 30 targets per generator and 6 generator categories per language.
    wide = df.pivot_table(
        index=["Level", "Target"], columns="Generator",
        values="Composite_0_4", aggfunc="mean"
    )
    generators = ["Claude", "DeepSeek", "GPT", "Gemini", "Qwen", "Baseline"]
    for g in generators:
        if g not in wide.columns:
            wide[g] = np.nan
    complete = wide.dropna(subset=generators).copy()
    complete["LLM_mean_0_4"] = complete[
        ["Claude", "DeepSeek", "GPT", "Gemini", "Qwen"]
    ].mean(axis=1)
    complete["Baseline_0_4"] = complete["Baseline"]
    complete["LLM_mean_percent"] = complete["LLM_mean_0_4"] / 4 * 100
    complete["Baseline_percent"] = complete["Baseline_0_4"] / 4 * 100

    generator_long = (
        wide.reset_index()
        .melt(id_vars=["Level", "Target"], value_vars=generators,
              var_name="Generator", value_name="Composite_0_4")
    )
    generator_long["Composite_percent"] = generator_long["Composite_0_4"] / 4 * 100
    return wide, complete, generator_long


def inferential_tables(wide, complete, language):
    gens = ["Claude", "DeepSeek", "GPT", "Gemini", "Qwen", "Baseline"]
    friedman_rows = []
    comparison_rows = []

    # Overall and level-specific analyses use complete target blocks.
    scopes = [("Overall", complete)] + [
        (lvl, complete[complete.index.get_level_values("Level").str.title() == lvl])
        for lvl in ["Easy", "Medium", "Hard"]
    ]
    for scope, sub in scopes:
        n = len(sub)
        if n >= 2:
            arrays = [sub[g].to_numpy(dtype=float) for g in gens]
            stat, pval = friedmanchisquare(*arrays)
            k = len(gens)
            kendall_w = stat / (n * (k - 1))
        else:
            stat = pval = kendall_w = np.nan
        friedman_rows.append({
            "Language": language, "Scope": scope, "Matched_targets": n,
            "Generators": len(gens), "Friedman_chi_square": stat,
            "df": len(gens)-1, "p_value": pval, "Kendall_W": kendall_w,
        })

        if n >= 2:
            try:
                wstat, wp = wilcoxon(
                    sub["LLM_mean_0_4"], sub["Baseline_0_4"],
                    alternative="two-sided", zero_method="wilcox",
                    method="auto"
                )
            except ValueError:
                wstat, wp = np.nan, np.nan
        else:
            wstat = wp = np.nan
        comparison_rows.append({
            "Language": language, "Scope": scope, "Matched_targets": n,
            "LLM_mean_percent": sub["LLM_mean_percent"].mean() if n else np.nan,
            "Baseline_percent": sub["Baseline_percent"].mean() if n else np.nan,
            "Wilcoxon_W": wstat, "p_raw": wp,
        })

    comp = pd.DataFrame(comparison_rows)
    # The paper describes Holm correction for comparison families but does not
    # enumerate the family membership in its text. We transparently apply Holm
    # to the four displayed tests (overall + three strata) within each language.
    valid = comp["p_raw"].notna()
    comp["p_Holm_within_language_4_tests"] = np.nan
    if valid.any():
        comp.loc[valid, "p_Holm_within_language_4_tests"] = holm_adjust(
            comp.loc[valid, "p_raw"].to_numpy()
        )
    comp["Holm_note"] = (
        "Holm over overall + Easy/Medium/Hard in this language; "
        "confirm family definition against the analysis plan before publication."
    )
    return pd.DataFrame(friedman_rows), comp


def coverage_audit(json_path):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    rows, checks = [], []

    for item in data["python"]["results"]:
        model, level = item["model"], item["difficulty"]
        ex, cov = item["execution"], item["coverage"]
        expected = PAPER_PYTHON[(model, level)]
        actual = (
            ex["pass_rate_percent"], cov["line_coverage_percent"],
            cov["branch_coverage_percent"], ex["coverage_reports"]
        )
        ok = all(close(a, e) for a, e in zip(actual, expected))
        checks.append({"Language":"Python","Generator":model,"Level":level,
                       "Check":"Table IV(a) exact cell","Expected":str(expected),
                       "Actual":str(actual),"PASS":ok})
        rows.append({
            "Language":"Python","Generator":model,"Level":level,
            "Pass_rate_percent":actual[0],"Line_coverage_percent":actual[1],
            "Branch_coverage_percent":actual[2],"Coverage_reports":actual[3],
            "Attempted_units":ex.get("attempted_test_units"),
        })

    for item in data["java"]["results"]:
        model, level = item["model"], item["difficulty"]
        ex, cov = item["execution"], item["coverage"]
        java_model_alias = {
            "Claude Fable 5.1": "Claude Fable 5.1",
            "DeepSeek-V4.1": "DeepSeek",
            "Gemini 3.8": "Gemini",
            "GPT-5.6": "GPT",
            "Qwen 3.8": "Qwen 3.8",
            "EvoSuite": "EvoSuite",
        }
        expected = PAPER_JAVA[(java_model_alias[model], level)]
        actual = (
            cov["line_coverage_percent"], ex["measured_targets"],
            ex["target_denominator"]
        )
        # Table IV prints one decimal place; Gemini Hard is additionally
        # documented at 26.48% in the supplementary paragraph.
        actual_for_table = (round(actual[0], 1), actual[1], actual[2])
        ok = all(close(a, e) for a, e in zip(actual_for_table, expected))
        checks.append({"Language":"Java","Generator":model,"Level":level,
                       "Check":"Table IV(b) exact cell","Expected":str(expected),
                       "Actual":str(actual),"PASS":ok})
        rows.append({
            "Language":"Java","Generator":model,"Level":level,
            "Line_coverage_percent":actual[0],
            "Measured_targets":actual[1],"Target_denominator":actual[2],
            "Measurement_rate_percent":ex["measurement_rate_percent"],
            "Branch_coverage_percent":cov.get("branch_coverage_percent"),
        })

    # Supplementary Gemini Java Hard values explicitly stated in paper.
    gh = next(x for x in data["java"]["results"]
              if x["model"] == "Gemini 3.8" and x["difficulty"] == "Hard")
    supp = {
        "line_coverage_percent": 26.48,
        "branch_coverage_percent": 19.77,
        "combined_line_branch_coverage_percent": 23.25,
        "attempted_runs": 50, "measured_runs": 50, "passed_runs": 30,
        "test_failure_runs": 20,
    }
    got = gh["coverage"]
    ex = gh["execution"]
    actual_supp = {
        "line_coverage_percent": got["line_coverage_percent"],
        "branch_coverage_percent": round(got["branch_coverage_percent"], 2),
        "combined_line_branch_coverage_percent": round(got["combined_line_branch_coverage_percent"], 2),
        "attempted_runs": ex["attempted_runs"], "measured_runs": ex["measured_runs"],
        "passed_runs": ex["passed_runs"], "test_failure_runs": ex["test_failure_runs"],
    }
    checks.append({"Language":"Java","Generator":"Gemini 3.8","Level":"Hard",
                   "Check":"Supplementary Gemini Hard paragraph",
                   "Expected":str(supp),"Actual":str(actual_supp),
                   "PASS":actual_supp == supp})
    return pd.DataFrame(rows), pd.DataFrame(checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", default="Execution_and_Coverage_Results(1).json")
    parser.add_argument("--python-xlsx", default="Python_Unit_Test_Evaluations_Revised_Strict_Aligned(1)(20261108-105627).xlsx")
    parser.add_argument("--java-xlsx", default="Java_Unit_Test_Evaluations_Corrected(4).xlsx")
    parser.add_argument("--output", default="SQAI_Reproduced_Statistics.xlsx")
    args = parser.parse_args()

    py, py_cols = load_scores(args.python_xlsx, "Python")
    ja, ja_cols = load_scores(args.java_xlsx, "Java")

    py_pool, py_level = criterion_tables(py, py_cols, "Python")
    ja_pool, ja_level = criterion_tables(ja, ja_cols, "Java")
    criteria = pd.concat([py_pool, ja_pool], ignore_index=True)
    criteria_by_level = pd.concat([py_level, ja_level], ignore_index=True)

    py_wide, py_complete, py_gen = matched_composites(py, "Python")
    ja_wide, ja_complete, ja_gen = matched_composites(ja, "Java")
    generator_composites = pd.concat([py_gen.assign(Language="Python"),
                                      ja_gen.assign(Language="Java")],
                                     ignore_index=True)

    py_friedman, py_wilcoxon = inferential_tables(py_wide, py_complete, "Python")
    ja_friedman, ja_wilcoxon = inferential_tables(ja_wide, ja_complete, "Java")
    friedman = pd.concat([py_friedman, ja_friedman], ignore_index=True)
    wilcoxon_results = pd.concat([py_wilcoxon, ja_wilcoxon], ignore_index=True)

    coverage, coverage_checks = coverage_audit(args.coverage_json)

    # Compare paper-highlighted pooled criterion means and paired composite means.
    sqai_checks = []
    for lang, pool in [("Python", py_pool), ("Java", ja_pool)]:
        expected = PAPER_SQAI[lang]
        for qnum, (paper_pct, paper_n) in expected.items():
            row = pool.loc[pool["Question"] == f"Q{qnum}"].iloc[0]
            ok = int(row["N"]) == paper_n and round(row["Mean_percent_of_4"], 1) == paper_pct
            sqai_checks.append({
                "Language":lang,"Check":f"Q{qnum} pooled mean and N",
                "Paper_mean_percent":paper_pct,"Calculated_mean_percent":row["Mean_percent_of_4"],
                "Paper_N":paper_n,"Calculated_N":int(row["N"]),"PASS":ok
            })

    for lang, complete, expected in [
        ("Python", py_complete, PAPER_COMPOSITE["Python"]),
        ("Java", ja_complete, PAPER_COMPOSITE["Java"]),
    ]:
        llm = complete["LLM_mean_percent"].mean()
        base = complete["Baseline_percent"].mean()
        n_ok = len(complete) == 28
        means_ok = round(llm, 2) == expected[0] and round(base, 2) == expected[1]
        sqai_checks.append({
            "Language":lang,"Check":"Paired composite means and matched N",
            "Paper_mean_percent":f"LLM {expected[0]} / Baseline {expected[1]}",
            "Calculated_mean_percent":f"LLM {llm:.8f} / Baseline {base:.8f}",
            "Paper_N":28,"Calculated_N":len(complete),"PASS":bool(n_ok and means_ok)
        })

    for lang, complete in [("Python", py_complete), ("Java", ja_complete)]:
        counts = complete.groupby(level=0).size().to_dict()
        ok = counts == {"Easy":10,"Hard":9,"Medium":9}
        sqai_checks.append({
            "Language":lang,"Check":"Matched target blocks by difficulty",
            "Paper_mean_percent":"Easy 10 / Medium 9 / Hard 9",
            "Calculated_mean_percent":str(counts),"Paper_N":28,
            "Calculated_N":len(complete),"PASS":ok
        })

    validation = pd.concat([coverage_checks, pd.DataFrame(sqai_checks)],
                           ignore_index=True, sort=False)

    # Run-level diagnostic counts from the workbooks.
    run_counts = []
    for lang, df in [("Python", py), ("Java", ja)]:
        for (model, level), g in df.groupby(["Model","Level"]):
            run_counts.append({
                "Language":lang,"Model":model,"Level":level,
                "Target_generator_records":len(g),
                "Rows_with_numeric_composite":int(g["Composite_0_4"].notna().sum()),
                "Rows_without_numeric_composite":int(g["Composite_0_4"].isna().sum()),
                "Mean_composite_0_4":g["Composite_0_4"].mean(),
                "Mean_composite_percent":g["Composite_percent"].mean(),
            })
    run_counts = pd.DataFrame(run_counts)

    output = Path(args.output)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        criteria.to_excel(writer, sheet_name="Pooled_Criterion_Profile", index=False)
        criteria_by_level.to_excel(writer, sheet_name="Criterion_By_Difficulty", index=False)
        generator_composites.to_excel(writer, sheet_name="Target_Composites", index=False)
        friedman.to_excel(writer, sheet_name="Friedman_KendallW", index=False)
        wilcoxon_results.to_excel(writer, sheet_name="Wilcoxon_Holm", index=False)
        coverage.to_excel(writer, sheet_name="Coverage_Table_IV", index=False)
        validation.to_excel(writer, sheet_name="Paper_Validation", index=False)
        run_counts.to_excel(writer, sheet_name="Workbook_Row_Summary", index=False)

    txt = output.with_name(output.stem + "_validation.txt")
    lines = [
        "SQAI PAPER REPRODUCTION AUDIT",
        f"Coverage JSON: {args.coverage_json}",
        f"Python SQAI workbook: {args.python_xlsx}",
        f"Java SQAI workbook: {args.java_xlsx}",
        "",
        f"Validation checks passed: {int(validation['PASS'].fillna(False).sum())}/{len(validation)}",
        "",
        "CHECK DETAILS:",
    ]
    for _, row in validation.iterrows():
        lines.append(f"[{'PASS' if bool(row.get('PASS')) else 'FAIL'}] "
                     f"{row.get('Language')} | {row.get('Generator', '')} | "
                     f"{row.get('Level', '')} | {row.get('Check', '')}")
    lines += [
        "",
        "INFERENTIAL TEST NOTE:",
        "Friedman chi-square, degrees of freedom, raw p-values, and Kendall's W are",
        "recalculated from the target-matched six-generator composite scores and checked",
        "against Table IV. Paired Wilcoxon tests compare the five-LLM target mean with",
        "the language-specific baseline. Holm-adjusted p-values are calculated across",
        "the four comparisons (Overall, Easy, Medium, Hard) separately within each",
        "language and checked against Table V. Raw Wilcoxon p-values and test statistics",
        "are retained in the output workbook as additional reproducibility details.",
        "",
        "INTERPRETATION:",
        "Python pass rate and Java measurement rate are distinct measures.",
        "N/A is excluded from applicable SQAI means and coverage N/A is not converted to zero.",
        "The Gemini Java Hard supplementary branch/combined values are checked to 2 decimals.",
    ]
    txt.write_text("\n".join(lines), encoding="utf-8")

    print(f"Created: {output.resolve()}")
    print(f"Created: {txt.resolve()}")
    print(f"Validation: {int(validation['PASS'].fillna(False).sum())}/{len(validation)} checks passed")
    if not validation["PASS"].fillna(False).all():
        print(validation.loc[~validation["PASS"].fillna(False)].to_string(index=False))
        raise SystemExit("Validation failed: inspect Paper_Validation sheet.")
    print("\nPaired composites:")
    print(wilcoxon_results[["Language","Scope","Matched_targets",
                            "LLM_mean_percent","Baseline_percent",
                            "Wilcoxon_W","p_raw",
                            "p_Holm_within_language_4_tests"]].to_string(index=False))
    print("\nFriedman/Kendall W:")
    print(friedman.to_string(index=False))


if __name__ == "__main__":
    main()
