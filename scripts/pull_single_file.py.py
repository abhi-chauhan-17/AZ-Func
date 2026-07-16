import subprocess
import sys
from pathlib import Path


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
# Fetch latest changes
# -----------------------------------------------------------
def fetch_remote():

    print("Fetching latest changes from GitHub...")

    run_command("git fetch origin")

    print("Fetch completed.")


# -----------------------------------------------------------
# Validate remote branch
# -----------------------------------------------------------
def validate_remote_branch(branch):
    """
    Checks whether the entered branch exists
    in the remote origin repository.
    """

    command = (
        f'git show-ref '
        f'--verify '
        f'--quiet '
        f'"refs/remotes/origin/{branch}"'
    )

    result = subprocess.run(
        command,
        shell=True
    )

    if result.returncode != 0:

        raise ValueError(
            f"Remote branch '{branch}' not found."
        )

    print(
        f"Remote branch '{branch}' found."
    )


# -----------------------------------------------------------
# Download python file from selected remote branch
# -----------------------------------------------------------
def download_remote_file(file_name, branch):

    print()
    print(f"Downloading : {file_name}")
    print(f"From Branch : {branch}")

    command = (
        f'git show origin/{branch}:{file_name}'
    )

    try:

        file_content = run_command(command)

    except RuntimeError:

        raise FileNotFoundError(
            f"{file_name} was not found "
            f"in remote branch '{branch}'."
        )

    temp_file_name = (
        f".__{Path(file_name).stem}_temp__.py"
    )

    temp_file = Path.cwd() / temp_file_name

    temp_file.write_text(
        file_content,
        encoding="utf-8"
    )

    print(
        f"Temporary File : "
        f"{temp_file.name}"
    )

    return temp_file


# -----------------------------------------------------------
# Convert downloaded python file
# -----------------------------------------------------------
def generate_notebook(temp_file):

    print()

    print("Generating Notebook...")

    command = (
        f'python scripts/convert_python_to_notebook.py "{temp_file}"'
    )

    run_command(command)

    print("Notebook Generated.")


# -----------------------------------------------------------
# Main
# -----------------------------------------------------------
def main():

    print("=" * 60)
    print("Pull Single Python File")
    print("=" * 60)

    fetch_remote()

    print()

    branch = input(
        "Enter branch name : "
    ).strip()

    if not branch:

        branch = "main"

    print()

    print(f"Selected Branch : {branch}")

    validate_remote_branch(branch)

    print()

    file_name = input(
        "Enter Python filename : "
    ).strip()

    if not file_name.endswith(".py"):

        file_name += ".py"

    print()

    print(f"Selected File : {file_name}")

    temp_file = download_remote_file(
        file_name,
        branch
    )

    try:

        generate_notebook(temp_file)

    finally:

        if temp_file.exists():

            temp_file.unlink()

            print()
            print(
                f"Temporary file deleted : "
                f"{temp_file.name}"
            )

# -----------------------------------------------------------
if __name__ == "__main__":

    temp_file = None

    try:

        temp_file = main()

    except Exception as ex:

        print("\nERROR")
        print(ex)

        sys.exit(1)

    finally:

        if temp_file and temp_file.exists():

            temp_file.unlink()

            print()
            print(f"Temporary file deleted : {temp_file.name}")