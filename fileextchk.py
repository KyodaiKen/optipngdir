import os
import argparse
import magic  # pip install python-magic-bin
from termcolor import colored  # pip install termcolor
from tqdm import tqdm  # pip install tqdm
import fnmatch
import sys

def normalize_path_for_windows(path):
    """Prepends \\?\ to the path on Windows to enable long paths."""
    if os.name == 'nt' and not path.startswith('\\\\?\\'):
        return '\\\\?\\' + os.path.abspath(path)
    return path

def check_and_fix_file_extension(directory, recursive=False, fix=False, masks=None, list_file=None):
    """
    Checks if the file extension of files in a directory (and optionally sub-directories)
    matches their detected MIME type and optionally renames them, with a progress bar and file mask.
    Optionally outputs mismatches to a file list.

    Args:
        directory (str): The path to the directory to check.
        recursive (bool, optional): Whether to check sub-directories as well. Defaults to False.
        fix (bool, optional): Whether to automatically rename files with incorrect extensions. Defaults to False.
        masks (list, optional): A list of filename patterns to include (e.g., ['*.png', '*.jpg']). Defaults to None (all files).
        list_file (str, optional): Filename to output mismatches with full path. Defaults to None.
    """
    if masks:
        print(f"Checking files in: {directory}{' (and sub-directories)' if recursive else ''}{' with auto-fix enabled' if fix else ''} with masks: {';'.join(masks)}{f' and outputting mismatches to {list_file}' if list_file else ''}")
    else:
        print(f"Checking files in: {directory}{' (and sub-directories)' if recursive else ''}{' with auto-fix enabled' if fix else ''}{f' and outputting mismatches to {list_file}' if list_file else ''}")

    total_files = 0
    mismatched_files = []
    for root, _, files in os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]:
        for item in files:
            if not masks or any(fnmatch.fnmatch(item, mask) for mask in masks):
                total_files += 1

    progress_bar = tqdm(total=total_files, desc="", unit="R 0", dynamic_ncols=True,
                        bar_format=colored("", 'light_magenta') + colored("{bar}", 'light_magenta') + colored("", 'light_magenta') +
                                           " {percentage:.2f}% {n_fmt}/{total_fmt} T {elapsed}/{remaining} {unit}")

    renamed_cnt = 0
    for root, _, files in os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]:
        for item in files:
            if not masks or any(fnmatch.fnmatch(item, mask) for mask in masks):
                item_path = os.path.join(root, item)
                long_path = normalize_path_for_windows(item_path)
                try:
                    try:
                        with open(long_path, 'rb') as f:
                            buffer = f.read(2048)
                            mime = magic.from_buffer(buffer, mime=True)
                    except OSError as e:
                        progress_bar.write(f"Error opening file: {item_path} - {e}")
                        continue  # Skip to the next file
                    name, ext = os.path.splitext(item)
                    ext = ext.lstrip('.').lower()

                    expected_exts = get_expected_extensions(mime)

                    if ext not in expected_exts and expected_exts != []:
                        progress_bar.write(f"Mismatch found: {item_path} (Extension: .{ext}, Content Type: {mime}) - Expected extensions: {', '.join(expected_exts)}")
                        if list_file:
                            mismatched_files.append(item_path + ", " + mime + " <> " + ";".join(expected_exts))
                        if fix and expected_exts:
                            new_ext = expected_exts[0]  # Take the first expected extension
                            new_filename = f"{name}.{new_ext}"
                            new_path = os.path.join(root, new_filename)
                            #On Windows NT make it POSIX
                            if os.name == 'nt' and not new_path.startswith('\\\\?\\'):
                                new_path = '\\\\?\\' + os.path.abspath(new_path)
                            if not os.path.exists(new_path):
                                os.rename(long_path, new_path)
                                progress_bar.write(f"  Renamed to: {os.path.join(root, new_filename)}")
                                renamed_cnt+=1
                                progress_bar.unit="R " + str(renamed_cnt)
                            else:
                                progress_bar.write(f"  Cannot rename to '{os.path.join(root, new_filename)}': File already exists.")
                        elif fix and not expected_exts:
                            progress_bar.write("  Cannot auto-fix: No suitable extension found for this content type.")
                    #else:
                        #progress_bar.write(f"Match: {item_path} (Extension: .{ext}, Content Type: {mime})")

                except Exception as e:
                    progress_bar.write(f"Error processing {item_path}: {e}")
                finally:
                    progress_bar.update(1)

    progress_bar.close()

    if list_file and mismatched_files:
        try:
            with open(list_file, 'w', encoding='utf-8') as f:
                for file_path in mismatched_files:
                    f.write(f"{file_path}\n")
            print(f"\nMismatched files list saved to: {list_file}")
        except Exception as e:
            print(f"Error writing mismatched files list to '{list_file}': {e}")

def get_expected_extensions(mime_type):
    """
    Provides a more comprehensive mapping of MIME types to common file extensions.
    This mapping can be expanded as needed. Based on common file formats.

    Args:
        mime_type (str): The detected MIME type.

    Returns:
        list: A list of expected file extensions (lowercase).
    """
    mapping = {
        "image/jpeg": ["jpg", "jpeg"],
        "image/png": ["png"],
        "image/gif": ["gif"],
        "image/webp": ["webp"],
        "image/tiff": ["tif", "tiff"],
        "image/x-ms-bmp": ["bmp"],
        "image/x-icon": ["ico"],
        "image/svg+xml": ["svg"],
        "image/avif": ["avif"],
        "image/heic": ["heic"],
        "image/heif": ["heif"], # HEIF is the container format, HEIC is a codec
        "image/jxl": ["jxl"],
        "image/x-tga": ["tga"],

        "application/pdf": ["pdf"],
        "text/plain": ["txt"],
        "text/csv": ["csv"],
        "application/json": ["json"],
        "application/xml": ["xml"],
        "text/html": ["html", "htm"],
        "text/css": ["css"],
        "text/javascript": ["js"],

        "audio/mpeg": ["mp3"],
        "audio/ogg": ["ogg"],
        "audio/x-wav": ["wav"],
        "audio/aac": ["aac"],
        "audio/x-flac": ["flac"],
        "audio/midi": ["mid", "midi"],

        "video/mp4": ["mp4"],
        "video/mpeg": ["mpg", "mpeg"],
        "video/webm": ["webm"],
        "video/x-msvideo": ["avi"],
        "video/quicktime": ["mov"],
        "video/x-matroska": ["mkv", "webm"],

        "application/zip": ["zip"],
        "application/x-tar": ["tar"],
        "application/gzip": ["gz"],
        "application/x-bzip2": ["bz2"],
        "application/x-7z-compressed": ["7z"],
        "application/x-rar-compressed": ["rar"],

        "application/msword": ["doc"],
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ["docx"],
        "application/vnd.ms-excel": ["xls"],
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ["xlsx"],
        "application/vnd.ms-powerpoint": ["ppt"],
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ["pptx"],

        "application/vnd.oasis.opendocument.text": ["odt"],
        "application/vnd.oasis.opendocument.spreadsheet": ["ods"],
        "application/vnd.oasis.opendocument.presentation": ["odp"],

        "application/x-executable": [], # Often no specific extension
        "application/octet-stream": [],  # Generic binary data
    }
    return mapping.get(mime_type, [])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Checks and optionally fixes file extensions based on content with a progress bar, file mask, and mismatch listing.")
    parser.add_argument("directory", help="The directory to check.")
    parser.add_argument("-R", "--recursive", action="store_true", help="Check sub-directories recursively.")
    parser.add_argument("--fix", action="store_true", help="Automatically rename files with incorrect extensions.")
    parser.add_argument("-m", "--mask", help="Filter files by name patterns (e.g., '*.png;*.jpg').", default=None)
    parser.add_argument("-l", "--list", help="Filename to output a list of mismatched files with full path.", default=None)

    args = parser.parse_args()

    masks = None
    if args.mask:
        masks = [m.strip() for m in args.mask.split(';') if m.strip()]

    if not os.path.isdir(args.directory):
        print(f"Error: Directory '{args.directory}' does not exist.")
    else:
        check_and_fix_file_extension(args.directory, args.recursive, args.fix, masks, args.list)
    