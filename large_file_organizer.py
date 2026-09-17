#!/usr/bin/env python3
"""
large_file_organizer.py

Scans one or more directory trees, finds the largest files and largest
directories, and stages the large files into a single organized "review"
folder using SYMLINKS (never copies, moves, or deletes originals).

Also writes CSV reports of the biggest files/directories, and generates a
(non-executed) shell script with `mv` commands you can review and run later
to actually relocate files to an external drive.

Usage examples:
    # Scan your home directory for anything >= 250MB
    python3 large_file_organizer.py --roots ~ --min-size-mb 250

    # Full-disk scan (best run with sudo for full access), staging results
    # under ~/LargeFilesReview, and prepare (but not run) a move script that
    # targets an attached drive mounted at /Volumes/BigDrive
    sudo python3 large_file_organizer.py --roots / --min-size-mb 500 \
        --output ~/LargeFilesReview --target-drive /Volumes/BigDrive

Nothing under --roots is ever modified, moved, copied, or deleted. The only
things written are the report files and symlinks inside --output.
"""

import argparse
import csv
import os
import sys
import time
from collections import defaultdict

# ---- Directories that are almost never useful / permission-heavy on macOS ----
DEFAULT_EXCLUDES = [
    "/System", "/dev", "/private/var/vm", "/private/var/db/dslocal",
    "/Volumes/com.apple.TimeMachine.localsnapshots",
    "/.Spotlight-V100", "/.fseventsd", "/.DocumentRevisions-V100",
    "/proc", "/net", "/private/tmp",
]

CATEGORY_EXTENSIONS = {
    "Videos": {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".tiff", ".bmp", ".raw", ".psd"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".bz2", ".xz"},
    "DiskImages_Installers": {".dmg", ".iso", ".pkg", ".app"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".txt", ".pages", ".key", ".numbers", ".rtf"},
    "Databases": {".sql", ".db", ".sqlite", ".sqlite3"},
    "VirtualMachines": {".vmdk", ".vdi", ".ova", ".ovf", ".qcow2", ".vhd"},
    "Backups": {".bak", ".backup", ".tm", ".timemachine"},
    "Code_Dev": {".log"},
}
EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORY_EXTENSIONS.items() for ext in exts}


def human_size(num_bytes):
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} EB"


def categorize(path):
    ext = os.path.splitext(path)[1].lower()
    return EXT_TO_CATEGORY.get(ext, "Other")


def is_excluded(path, excludes):
    for ex in excludes:
        if path == ex or path.startswith(ex.rstrip("/") + "/"):
            return True
    return False


def scan_tree(root, min_size_bytes, excludes, large_files, dir_sizes, errors):
    """
    Recursively walk `root` without following symlinks, accumulating:
      - large_files: list of (size, path, mtime)
      - dir_sizes: dict path -> cumulative size of everything under it
    Returns the total size of `root`.
    """
    total = 0
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except (PermissionError, FileNotFoundError, NotADirectoryError) as e:
        errors.append(f"{root}: {e}")
        return 0
    except OSError as e:
        errors.append(f"{root}: {e}")
        return 0

    for entry in entries:
        path = entry.path
        if is_excluded(path, excludes):
            continue
        try:
            if entry.is_symlink():
                continue  # never follow symlinks (avoids loops/double counting)
            if entry.is_dir(follow_symlinks=False):
                total += scan_tree(path, min_size_bytes, excludes, large_files, dir_sizes, errors)
            elif entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                size = st.st_size
                total += size
                if size >= min_size_bytes:
                    large_files.append((size, path, st.st_mtime))
        except (PermissionError, FileNotFoundError, OSError) as e:
            errors.append(f"{path}: {e}")
            continue

    dir_sizes[root] = total
    return total


def unique_dest(dest_dir, name):
    candidate = os.path.join(dest_dir, name)
    if not os.path.lexists(candidate):
        return candidate
    base, ext = os.path.splitext(name)
    i = 2
    while True:
        candidate = os.path.join(dest_dir, f"{base}__{i}{ext}")
        if not os.path.lexists(candidate):
            return candidate
        i += 1


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Find large files/directories and stage them for review (no deletions).")
    parser.add_argument("--roots", nargs="+", default=[os.path.expanduser("~")],
                         help="Directories to scan (default: your home folder).")
    parser.add_argument("--min-size-mb", type=float, default=250,
                         help="Minimum file size in MB to be considered 'large' (default: 250).")
    parser.add_argument("--output", default=os.path.expanduser("~/LargeFilesReview"),
                         help="Folder to create with reports + organized symlinks.")
    parser.add_argument("--top-dirs", type=int, default=50,
                         help="How many of the largest directories to report.")
    parser.add_argument("--exclude", nargs="*", default=[],
                         help="Additional absolute paths to exclude from scanning.")
    parser.add_argument("--target-drive", default=None,
                         help="Path to an attached drive (e.g. /Volumes/BigDrive). "
                              "If given, a review-only move_to_target_drive.sh script "
                              "is generated (not executed) for later use.")
    args = parser.parse_args()

    roots = [os.path.abspath(os.path.expanduser(r)) for r in args.roots]
    excludes = DEFAULT_EXCLUDES + [os.path.abspath(os.path.expanduser(e)) for e in args.exclude]
    min_size_bytes = int(args.min_size_mb * 1024 * 1024)
    output_dir = os.path.abspath(os.path.expanduser(args.output))

    print(f"Scanning roots: {roots}")
    print(f"Minimum size threshold: {human_size(min_size_bytes)}")
    print("This may take a while for large drives...\n")

    large_files = []
    dir_sizes = {}
    errors = []

    start = time.time()
    for root in roots:
        if not os.path.isdir(root):
            print(f"WARNING: root '{root}' is not a directory, skipping.")
            continue
        scan_tree(root, min_size_bytes, excludes, large_files, dir_sizes, errors)
    elapsed = time.time() - start

    large_files.sort(key=lambda t: t[0], reverse=True)
    biggest_dirs = sorted(dir_sizes.items(), key=lambda t: t[1], reverse=True)[: args.top_dirs]

    os.makedirs(output_dir, exist_ok=True)
    reports_dir = os.path.join(output_dir, "reports")
    by_category_dir = os.path.join(output_dir, "by_category")
    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(by_category_dir, exist_ok=True)

    # --- Reports ---
    write_csv(
        os.path.join(reports_dir, "large_files.csv"),
        ["size_bytes", "size_human", "category", "modified", "path"],
        [
            (size, human_size(size), categorize(path),
             time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)), path)
            for size, path, mtime in large_files
        ],
    )
    write_csv(
        os.path.join(reports_dir, "large_directories.csv"),
        ["size_bytes", "size_human", "path"],
        [(size, human_size(size), path) for path, size in biggest_dirs],
    )
    if errors:
        with open(os.path.join(reports_dir, "scan_errors.log"), "w") as f:
            f.write("\n".join(errors))

    # --- Organize large files into by_category via symlinks (no data is duplicated) ---
    category_counts = defaultdict(int)
    move_lines = []
    if args.target_drive:
        target_root = os.path.abspath(os.path.expanduser(args.target_drive))
        move_lines.append("#!/bin/bash")
        move_lines.append("# Review this script before running it. Nothing here runs automatically.")
        move_lines.append("# It moves the ORIGINAL large files (not the symlinks) to your external drive,")
        move_lines.append("# preserving the same category organization created under by_category/.")
        move_lines.append("set -euo pipefail")

    for size, path, mtime in large_files:
        cat = categorize(path)
        cat_dir = os.path.join(by_category_dir, cat)
        os.makedirs(cat_dir, exist_ok=True)
        link_name = os.path.basename(path)
        dest = unique_dest(cat_dir, link_name)
        try:
            os.symlink(path, dest)
        except OSError as e:
            errors.append(f"symlink {path} -> {dest}: {e}")
            continue
        category_counts[cat] += 1

        if args.target_drive:
            target_cat_dir = os.path.join(target_root, cat)
            dest_target = os.path.join(target_cat_dir, link_name)
            move_lines.append(f'mkdir -p "{target_cat_dir}"')
            move_lines.append(f'mv -n "{path}" "{dest_target}"')

    if args.target_drive:
        move_script_path = os.path.join(output_dir, "move_to_target_drive.sh")
        with open(move_script_path, "w") as f:
            f.write("\n".join(move_lines) + "\n")
        os.chmod(move_script_path, 0o755)

    # --- Summary ---
    total_large_bytes = sum(s for s, _, _ in large_files)
    print(f"Scan finished in {elapsed:.1f}s")
    print(f"Large files found (>= {human_size(min_size_bytes)}): {len(large_files)}")
    print(f"Total size of large files: {human_size(total_large_bytes)}\n")

    print("Top 10 largest files:")
    for size, path, _ in large_files[:10]:
        print(f"  {human_size(size):>10}  {path}")

    print("\nTop 10 largest directories:")
    for path, size in biggest_dirs[:10]:
        print(f"  {human_size(size):>10}  {path}")

    print("\nBy category (symlinked into review folder):")
    for cat, count in sorted(category_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<25} {count} files")

    print(f"\nReview folder: {output_dir}")
    print(f"  - reports/large_files.csv")
    print(f"  - reports/large_directories.csv")
    print(f"  - by_category/<Category>/  (symlinks to originals, nothing was copied/moved)")
    if args.target_drive:
        print(f"  - move_to_target_drive.sh (generated, NOT run — review before executing)")
    if errors:
        print(f"\n{len(errors)} paths could not be read (permissions, etc). See reports/scan_errors.log")
        print("Tip: re-run with 'sudo' for a more complete system-wide scan.")


if __name__ == "__main__":
    main()
