#!/usr/bin/env python3
import os
import sys
import zipfile
import subprocess
from pathlib import Path


def run_git_command(args, repo_path: Path) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def get_files_to_archive(repo_path: Path) -> list[str]:
    output = run_git_command(
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        repo_path,
    )
    files = output.decode("utf-8", errors="surrogateescape").split("\0")
    return [f for f in files if f]


def make_zip(repo_path: Path, output_zip: Path) -> None:
    files = get_files_to_archive(repo_path)

    output_zip = output_zip.resolve()
    if output_zip.exists():
        output_zip.unlink()

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files:
            abs_path = repo_path / rel_path

            if not abs_path.exists():
                continue

            if abs_path.resolve() == output_zip:
                continue

            zf.write(abs_path, arcname=rel_path)

    print(f"Created: {output_zip}")
    print(f"Added files: {len(files)}")


def main():
    repo_path = Path.cwd()

    if len(sys.argv) > 1:
        output_zip = Path(sys.argv[1])
    else:
        output_zip = repo_path / f"{repo_path.name}.zip"

    try:
        run_git_command(["rev-parse", "--show-toplevel"], repo_path)
    except subprocess.CalledProcessError:
        print("Error: this directory is not a Git repository.", file=sys.stderr)
        sys.exit(1)

    make_zip(repo_path, output_zip)


if __name__ == "__main__":
    main()
