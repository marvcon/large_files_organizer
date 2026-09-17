# large_files_organizer
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
