"""Validate notebook structure, embedded source and optionally run its extracted runtime suite.

This verifies local execution. It does not impersonate a Google account or claim
that Colab's authenticated iframe/Drive/Secrets APIs were exercised.
"""

import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def validate(path, execute_smoke=False):
    notebook = json.loads(Path(path).read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4 and notebook["nbformat_minor"] == 5
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    for cell in code_cells:
        assert cell["outputs"] == [] and cell["execution_count"] is None
        compile(cell["source"], f"<notebook:{cell['id']}>", "exec")
    extraction = next(c["source"] for c in code_cells if "BUNDLE_B64 =" in c["source"])
    constants = {}
    for node in ast.parse(extraction).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"BUNDLE_B64", "BUNDLE_SHA256"}:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
    blob = base64.b64decode(constants["BUNDLE_B64"], validate=True)
    assert hashlib.sha256(blob).hexdigest() == constants["BUNDLE_SHA256"]
    assert constants["BUNDLE_SHA256"] == notebook["metadata"]["smartgrade_bundle_sha256"]
    with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
        names = bundle.namelist()
        forbidden = {
            ".git",
            ".venv",
            "storage",
            "data",
            "outputs",
            "work",
            ".colab",
            "notebooks",
            "__pycache__",
        }
        for name in names:
            assert not (set(Path(name).parts) & forbidden), f"Unexpected runtime artifact: {name}"
            assert Path(name).name != ".env"
            assert bundle.read(name) == (ROOT / name).read_bytes(), f"Embedded source is stale: {name}"
    print(
        f"Notebook valid: {len(code_cells)} Python cells; {len(names)} embedded source files; no saved outputs/data"
    )
    if execute_smoke:
        old_root = os.environ.get("SMARTGRADE_COLAB_ROOT")
        with tempfile.TemporaryDirectory(prefix="smartgrade-notebook-") as temporary:
            try:
                os.environ["SMARTGRADE_COLAB_ROOT"] = temporary
                scope = {}
                exec(code_cells[0]["source"], scope)
                exec(extraction, scope)
                exec(extraction, scope)  # Running setup again must preserve existing matching files.
                project = scope["PROJECT_DIR"]
                for name in names:
                    assert (project / name).is_file()
                app = project / "app.py"
                original = app.read_bytes()
                app.write_bytes(original + b"\n# locally edited test marker\n")
                try:
                    exec(extraction, scope)
                except FileExistsError:
                    assert app.read_bytes().endswith(b"# locally edited test marker\n")
                else:
                    raise AssertionError("Extraction overwrote a user edit")
                app.write_bytes(original)
                subprocess.run(
                    [sys.executable, "-m", "scripts.generate_omr", "--output", "outputs/demo"],
                    cwd=project,
                    check=True,
                )
                subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"],
                    cwd=project,
                    check=True,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(project),
                        "SMARTGRADE_DATA_DIR": str(Path(temporary) / "test-data"),
                        "ALLOW_EXTERNAL_AI": "false",
                        "ALLOW_PAID_API": "false",
                    },
                )
                print("Extracted notebook runtime suite passed locally; Colab account APIs were not called.")
            finally:
                if old_root is None:
                    os.environ.pop("SMARTGRADE_COLAB_ROOT", None)
                else:
                    os.environ["SMARTGRADE_COLAB_ROOT"] = old_root
                sys.path[:] = [p for p in sys.path if p != str(scope.get("PROJECT_DIR", ""))]
    return notebook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notebook", type=Path, default=ROOT / "notebooks/SmartGrade_Colab.ipynb")
    parser.add_argument("--execute-smoke", action="store_true")
    args = parser.parse_args()
    validate(args.notebook, args.execute_smoke)


if __name__ == "__main__":
    main()
