# ==============================================================================
# SPARK LAUNCHER — MilSearch
# ==============================================================================
# PURPOSE:
#     Run a Spark job of spark_jobs/ with spark-submit in the Docker image of the course
#     (iisas/hadoop-spark-pig-hive:2.9.2: Spark 2.4.3, Python 3.6, Java 8).
#
# RESPONSIBILITIES:
#     1. Pack the Python packages of the jobs into dist/milsearch-spark.zip (spark-submit --py-files).
#     2. Start the container with the project directory mounted at /app and run spark-submit.
#
# USAGE:
#     python -m tools.spark extract_pages
#     python -m tools.spark wiki_pages --lang en
#     python -m tools.spark wiki_join --cores 16 --memory 24g
#
# INVARIANTS:
#     The launcher only builds the command. On the cluster, run the same spark-submit command
#     with --master yarn (or spark://...) and --data-dir hdfs:///... .
# ==============================================================================

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

# ==============================================================================
# CONSTANTS
# ==============================================================================

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ZIP_PATH: Path = PROJECT_ROOT / "dist" / "milsearch-spark.zip"
DOCKER_IMAGE = "iisas/hadoop-spark-pig-hive:2.9.2"
PACKAGES: tuple[str, ...] = ("extractor", "wiki", "spark_jobs")
JOBS: tuple[str, ...] = ("extract_pages", "wiki_pages", "wiki_anchors", "wiki_join")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def build_py_files_zip() -> Path:
    """
    Pack the Python packages of the Spark jobs into one ZIP file for --py-files.

    Steps:
        1. Add each .py file of the packages with its package path.

    Returns:
        Path of the ZIP file.
    """
    # 1. Add each .py file of the packages with its package path
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for package in PACKAGES:
            for path in sorted((PROJECT_ROOT / package).glob("*.py")):
                archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
    return ZIP_PATH


def build_command(job: str, cores: int, memory: str, job_arguments: list[str]) -> list[str]:
    """
    Build the docker command that runs spark-submit for one job.

    Steps:
        1. Mount the project at /app; use Python 3 and UTF-8 in the container.
        2. Run spark-submit in local mode with the cores and the driver memory.

    Returns:
        Command as a list of arguments.
    """
    # 1. Mount the project at /app; use Python 3 and UTF-8 in the container
    command = [
        "docker", "run", "--rm", "--name", f"milsearch-spark-{job.replace('_', '-')}",
        "-v", f"{PROJECT_ROOT}:/app", "-w", "/app",
        "-e", "PYSPARK_PYTHON=python3", "-e", "PYTHONIOENCODING=utf-8", "-e", "LC_ALL=C.UTF-8", "-e", "LANG=C.UTF-8",
        "--entrypoint", "spark-submit", DOCKER_IMAGE,
    ]

    # 2. Run spark-submit in local mode with the cores and the driver memory
    command += [
        "--master", f"local[{cores}]", "--driver-memory", memory,
        "--conf", "spark.local.dir=/tmp/spark", "--conf", "spark.sql.shuffle.partitions=64",
        "--conf", "spark.driver.maxResultSize=4g", "--conf", "spark.ui.showConsoleProgress=false",
        "--py-files", ZIP_PATH.relative_to(PROJECT_ROOT).as_posix(),
        f"spark_jobs/{job}.py", *job_arguments,
    ]
    return command


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Run one Spark job in Docker.

    Steps:
        1. Parse the launcher options; pass the other arguments to the job.
        2. Build the ZIP file and run the command; return its exit code.

    Returns:
        None.
    """
    # 1. Parse the launcher options; pass the other arguments to the job
    parser = argparse.ArgumentParser(description="Run a MilSearch Spark job in the Docker image of the course.")
    parser.add_argument("job", choices=JOBS)
    parser.add_argument("--cores", type=int, default=os.cpu_count() or 4, help="Local Spark cores (default: all).")
    parser.add_argument("--memory", default="20g", help="Driver memory (local mode: driver = executor).")
    arguments, job_arguments = parser.parse_known_args()

    # 2. Build the ZIP file and run the command; return its exit code
    build_py_files_zip()
    command = build_command(arguments.job, arguments.cores, arguments.memory, job_arguments)
    print(" ".join(command), flush=True)
    sys.exit(subprocess.call(command, env={**os.environ, "MSYS_NO_PATHCONV": "1"}))


if __name__ == "__main__":
    main()
