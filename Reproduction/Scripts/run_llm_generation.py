from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from litellm import completion

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "Reproduction" / ".env")

MODEL_NAME = os.environ.get("LLM_MODEL", "").strip()
if not MODEL_NAME:
    raise RuntimeError(
        "Set LLM_MODEL in Reproduction/.env before running this script. "
        "Use the provider/model identifier required by LiteLLM."
    )

def model_call(prompt: str) -> str:
    """Send one independent prompt to the selected model."""
    response = completion(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()

def main() -> None:
    model_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", MODEL_NAME).strip("._") or "selected_model"
    os.environ.setdefault("SQAI_INPUT_DIR", "/content")
    os.environ.setdefault("SQAI_OUTPUT_DIR", f"/content/generated_results/{model_slug}")
    os.environ.setdefault("SQAI_CHECKPOINT_DIR", f"/content/experiment_checkpoints/{model_slug}")

    # Import after setting the run-specific directories.
    from model_agnostic_test_generation import run_experiment
    outputs = run_experiment(model_call)
    print("\nGenerated output workbooks:")
    for path in outputs:
        print(path)

if __name__ == "__main__":
    main()
