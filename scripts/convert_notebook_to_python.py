import re
import subprocess
import sys
from pathlib import Path
import nbformat


# -----------------------------------------------------------
# Execute shell command
# -----------------------------------------------------------
def run_command(command):
    """
    Executes a shell command.
    Raises an exception if the command fails.
    """
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


# -----------------------------------------------------------
# Get staged notebooks
# -----------------------------------------------------------
def get_staged_notebooks():
    """
    Returns only staged notebook files.
    """

    output = run_command(
        "git diff --cached --name-only"
    )

    notebooks = []

    for file in output.splitlines():

        if file.lower().endswith(".ipynb"):

            notebooks.append(Path(file))

    return notebooks


# -----------------------------------------------------------
# Clean notebook filename
# -----------------------------------------------------------
def clean_filename(notebook_path):

    filename = notebook_path.stem.lower()

    filename = re.sub(r'_\d{8}$', '', filename)

    filename = re.sub(r'_+', '_', filename)

    filename = filename.strip("_")

    return filename + ".py"

# -----------------------------------------------------------
# Find matching python file
# -----------------------------------------------------------
def find_existing_python_file(clean_python_name):

    target = Path(clean_python_name).stem.lower()

    for py_file in Path.cwd().glob("*.py"):

        if py_file.stem.lower() == target:

            return py_file

    return None


# -----------------------------------------------------------
# Convert notebook to exact Python code
# -----------------------------------------------------------
def convert_notebook(notebook_path, python_filename):
    """
    Extracts only actual code cells from the notebook.

    No shebang.
    No encoding header.
    No In[] markers.
    No notebook metadata.
    No cell outputs.
    """

    print(f"Converting : {notebook_path.name}")

    with notebook_path.open(
        "r",
        encoding="utf-8"
    ) as notebook_file:

        notebook = nbformat.read(
            notebook_file,
            as_version=4
        )

    code_blocks = []

    for cell in notebook.cells:

        if cell.cell_type == "code":

            code = cell.source

            if code.strip():

                code_blocks.append(code)

    python_content = "\n\n".join(code_blocks)

    existing_py = find_existing_python_file(
        python_filename
    )

    if existing_py:

        output_file = existing_py

        print(
            f"Updating existing file : "
            f"{existing_py.name}"
        )

    else:

        output_file = (
            notebook_path.parent
            / python_filename.lower()
        )

        print(
            f"Creating new file : "
            f"{output_file.name}"
        )

    output_file.write_text(
        python_content,
        encoding="utf-8"
    )

    return output_file


# -----------------------------------------------------------
# Stage generated python file
# -----------------------------------------------------------
def stage_python_file(py_file):
    """
    Adds generated python file to Git staging.
    """

    run_command(f'git add "{py_file}"')


# -----------------------------------------------------------
# Remove notebook from staging
# -----------------------------------------------------------
def unstage_notebook(notebook):
    """
    Removes notebook from Git staging.

    The notebook remains on the local machine,
    but it will not be committed to GitHub.
    """

    run_command(
        f'git restore --staged "{notebook}"'
    )

# -----------------------------------------------------------
# Main
# -----------------------------------------------------------
def main():

    print("=" * 60)
    print("Notebook Conversion Started")
    print("=" * 60)

    notebooks = get_staged_notebooks()

    if not notebooks:

        print("No staged notebooks found.")

        return

    for notebook in notebooks:

        print(f"\nNotebook : {notebook.name}")

        python_name = clean_filename(notebook)

        print(f"Python   : {python_name}")

        py_file = convert_notebook(
            notebook,
            python_name
        )

        stage_python_file(py_file)

        unstage_notebook(notebook)

        print("Completed")

    print("\nAll notebooks processed successfully.")


# -----------------------------------------------------------
if __name__ == "__main__":

    try:
        main()

    except Exception as ex:

        print("\nERROR")

        print(ex)

        sys.exit(1)