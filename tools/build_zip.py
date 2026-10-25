# ==============================================================================
# BUILD ZIP — MilSearch
# ==============================================================================
# PURPOSE:
#     Make the ZIP file with the source code for the wiki submission.
#
# RESPONSIBILITIES:
#     1. Get the project files from git: tracked files and new files that .gitignore does not exclude.
#     2. Write them into dist/MilSearch-VINF-part1.zip under the folder "MilSearch/".
#
# USAGE:
#     python -m tools.build_zip
#
# INVARIANTS:
#     The ZIP does not contain data/, .venv/, caches or dist/ (see .gitignore).
#     The course wants ZIP, not RAR.
# ==============================================================================

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

# ==============================================================================
# CONSTANTS
# ==============================================================================

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ZIP_PATH: Path = PROJECT_ROOT / "dist" / "MilSearch-VINF-part1.zip"
ARCHIVE_FOLDER = "MilSearch"


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def get_project_files() -> list[str]:
    """
    Get the project files that git tracks or that .gitignore does not exclude.

    Steps:
        1. Run "git ls-files" for tracked and untracked, not ignored files.
        2. Keep only files that exist (a deleted tracked file stays in the list until a commit).

    Returns:
        Sorted relative paths with "/" separators.
    """
    # 1. Run "git ls-files" for tracked and untracked, not ignored files
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    ).stdout

    # 2. Keep only files that exist
    return sorted({path for path in output.splitlines() if (PROJECT_ROOT / path).is_file()})


def build_zip(paths: list[str], zip_path: Path) -> None:
    """
    Write the files into a ZIP file.

    Steps:
        1. Make the output directory.
        2. Add each file under the archive folder with compression.

    Returns:
        None.
    """
    # 1. Make the output directory
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Add each file under the archive folder with compression
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(PROJECT_ROOT / path, f"{ARCHIVE_FOLDER}/{path}")


# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    """
    Build the submission ZIP and show its size.

    Steps:
        1. Get the project files and write the ZIP.
        2. Show the number of files and the size.

    Returns:
        None.
    """
    # 1. Get the project files and write the ZIP
    paths = get_project_files()
    build_zip(paths, ZIP_PATH)

    # 2. Show the number of files and the size
    print(f"SAVED zip | files_count={len(paths)} size_kb={ZIP_PATH.stat().st_size / 1024:.0f} "
          f"path={ZIP_PATH.relative_to(PROJECT_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
