import subprocess
import sys
from pathlib import Path
from datetime import datetime
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
# Today's date
# -----------------------------------------------------------
def get_today():

    return datetime.today().strftime("%Y%m%d")


# -----------------------------------------------------------
# Build notebook filename
# -----------------------------------------------------------
def build_notebook_name(py_file):
    """
    Examples

    customer.py
        -> customer_20260703.ipynb

    .__customer_temp__.py
        -> customer_20260703.ipynb
    """

    today = get_today()

    name = py_file.stem

    # Handle temporary pull file
    if name.startswith(".__") and name.endswith("_temp__"):

        name = name.replace(".__", "", 1)
        name = name.replace("_temp__", "")

    notebook_name = f"{name}_{today}.ipynb"

    return Path.cwd() / notebook_name


# -----------------------------------------------------------
# Convert Python to Notebook
# -----------------------------------------------------------
def convert_python_to_notebook(py_file):

    if not py_file.exists():

        raise FileNotFoundError(
            f"{py_file.name} not found."
        )

    notebook_file = build_notebook_name(py_file)

    print(f"\nPython   : {py_file.name}")
    print(f"Notebook : {notebook_file.name}")

    python_content = py_file.read_text(
        encoding="utf-8"
    )

    notebook = nbformat.v4.new_notebook()

    notebook.cells = [
        nbformat.v4.new_code_cell(
            source=python_content
        )
    ]

    with notebook_file.open(
        "w",
        encoding="utf-8"
    ) as output_file:

        nbformat.write(
            notebook,
            output_file
        )

    print("Completed")

    return notebook_file


# -----------------------------------------------------------
# Main
# -----------------------------------------------------------
def main():

    print("=" * 60)
    print("Python To Notebook Conversion")
    print("=" * 60)

    if len(sys.argv) < 2:

        print("\nNo Python file supplied.")

        return

    for file in sys.argv[1:]:

        convert_python_to_notebook(
            Path(file)
        )

    print("\nAll files processed successfully.")


# -----------------------------------------------------------
if __name__ == "__main__":

    try:

        main()

    except Exception as ex:

        print("\nERROR")

        print(ex)

        sys.exit(1)
