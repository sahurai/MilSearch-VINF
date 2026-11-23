# ==============================================================================
# LUCENE LAUNCHER — MilSearch
# ==============================================================================
# PURPOSE:
#     Run a module of lucene_index/ with PyLucene in Docker. PyLucene needs a JVM and
#     native JCC bindings; the image coady/pylucene:10 has PyLucene 10.0.0 (Lucene 10.0.0),
#     Java and Python 3.15, so no local installation is necessary.
#
# RESPONSIBILITIES:
#     1. Start the container with the project directory mounted at /app.
#     2. Run "python -m lucene_index.<module>" with the given arguments; return its exit code.
#
# USAGE:
#     python -m tools.lucene build                       # build data/lucene/index
#     python -m tools.lucene search "tiger OR panther"   # search (Lucene query syntax)
#     python -m tools.lucene search                      # interactive search (one JVM start)
#     python -m tools.lucene compare                     # own index vs Lucene index report
#     python -m tools.lucene test                        # Lucene tests in the container
#
# INVARIANTS:
#     The launcher only builds and runs the docker command; the modules do the work.
# ==============================================================================

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ==============================================================================
# CONSTANTS
# ==============================================================================

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DOCKER_IMAGE = "coady/pylucene:10"
COMMANDS: dict[str, str] = {
    "build": "lucene_index.build",
    "search": "lucene_index.search",
    "compare": "lucene_index.compare",
}
TEST_PATHS: tuple[str, ...] = ("tests/unit/test_lucene_schema.py", "tests/integration/test_lucene_index.py")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def build_command(container_command: list[str]) -> list[str]:
    """
    Build the docker command that runs one command in the PyLucene container.

    Steps:
        1. Mount the project at /app; keep stdin open for the interactive search; use UTF-8.

    Returns:
        Command as a list of arguments.
    """
    # 1. Mount the project at /app; keep stdin open for the interactive search; use UTF-8
    return [
        "docker", "run", "--rm", "-i",
        "-v", f"{PROJECT_ROOT}:/app", "-w", "/app",
        "-e", "PYTHONIOENCODING=utf-8", "-e", "PYTHONDONTWRITEBYTECODE=1",
        DOCKER_IMAGE, *container_command,
    ]


def run_module(module: str, arguments: list[str]) -> int:
    """
    Run one Python module in the PyLucene container.

    Steps:
        1. Build the command and run it; the container output goes to this terminal.

    Returns:
        Exit code of the container.
    """
    # 1. Build the command and run it; the container output goes to this terminal
    command = build_command(["python", "-m", module, *arguments])
    return subprocess.call(command, env={**os.environ, "MSYS_NO_PATHCONV": "1"})


def run_tests(arguments: list[str]) -> int:
    """
    Run the Lucene tests in the container (pytest is installed in the temporary container).

    Steps:
        1. Install pytest quietly and run the Lucene test files.

    Returns:
        Exit code of pytest.
    """
    # 1. Install pytest quietly and run the Lucene test files
    script = ("pip install --quiet --disable-pip-version-check --root-user-action=ignore pytest && "
              f"python -m pytest -p no:cacheprovider {' '.join(TEST_PATHS)} {' '.join(arguments)}")
    return subprocess.call(build_command(["sh", "-c", script]), env={**os.environ, "MSYS_NO_PATHCONV": "1"})


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Run a Lucene command in Docker.

    Steps:
        1. Read the command name; show the usage for an unknown command.
        2. Run the tests or the module of the command with the other arguments.

    Returns:
        None.
    """
    # 1. Read the command name; show the usage for an unknown command
    if len(sys.argv) < 2 or sys.argv[1] not in (*COMMANDS, "test"):
        sys.exit(f"usage: python -m tools.lucene {{{','.join((*COMMANDS, 'test'))}}} [arguments]")

    # 2. Run the tests or the module of the command with the other arguments
    command, arguments = sys.argv[1], sys.argv[2:]
    sys.exit(run_tests(arguments) if command == "test" else run_module(COMMANDS[command], arguments))


if __name__ == "__main__":
    main()
