import subprocess
import os
import sys
import shutil
import platform
import signal
import unicodedata
import argparse
import threading
import time
import json
import re
import hashlib
from termcolor import colored
from tqdm import tqdm

# ANSI escape code to switch to the alternate screen
enter_alt_screen = "\033[?1049h"
# ANSI escape code to switch back to the normal screen
exit_alt_screen = "\033[?1049l"

# Global flag to indicate if a clean exit has been requested
exit_requested = False

UNICODE_DETECT_REGEX = re.compile(r'[^\x00-\x7F]')  # Matches any non-ASCII character

termlines = []
errlines = []

def printl(strIn):
    global termlines
    termlines.append(strIn)
    print(strIn)

def has_unicode(filename):
    """Checks if a filename contains Unicode characters."""
    return bool(UNICODE_DETECT_REGEX.search(filename))

def get_path_root(path):
    """
    Gets the root of a path, handling Windows drives and attempting to
    identify Linux mount points (like /mnt/nas).
    """
    drive = os.path.splitdrive(path)[0]
    if drive:
        return drive
    elif os.path.isabs(path):
        parts = path.split('/')
        if len(parts) > 1 and parts[1]:
            if parts[1] in ('mnt', 'media', 'opt'): # Common mount point directories
                if len(parts) > 2 and parts[2]:
                    return '/' + parts[1] + '/' + parts[2]
                else:
                    return '/' + parts[1]
            else:
                return '/' + parts[1]
        elif path == '/':
            return '/'
    return ''

def generate_temp_filename(filename):
    """Generates a temporary filename based on the hash of the original (Unicode-aware)."""
    fullfilename = os.path.abspath(filename)
    basepath = get_path_root(fullfilename)
    if basepath.endswith(":") and os.name == 'nt':
        basepath = basepath + "\\"
    hashword = fullfilename.encode('utf-8')
    hashed = hashlib.md5(hashword).hexdigest()
    return os.path.join(basepath, hashed)

def get_terminal_width():
    try:
        return shutil.get_terminal_size().columns
    except OSError:
        return 80  # Default width if unable to get

def shorten_filename(filename, max_width):
    """Shortens a filename to fit within the maximum width, preserving the extension."""
    if len(filename) <= max_width:
        return filename

    name, ext = os.path.splitext(os.path.basename(filename))

    if len(name) + len(ext) + 3 <= max_width:  # 3 for the ellipsis
        return name + "..." + ext
    elif len(ext) + 3 >= max_width:
        return "..." + ext[:max_width - 3]
    elif 3 >= max_width:
        return filename[:max_width] # Very short width, just truncate

    available_width = max_width - len(ext) - 3
    if available_width > 0:
        return name[:available_width] + "..." + ext
    else:
        return "..." + ext # Should not happen with the earlier checks, but as a fallback

def get_os():
    """Determines the operating system."""
    if sys.platform.startswith('linux'):
        return "Linux"
    elif sys.platform.startswith('darwin'):
        return "macOS"
    elif sys.platform.startswith('win'):
        return "Windows"
    else:
        return platform.system()  # Fallback to the more general platform.system()

def get_modified_time_long_path(filepath):
    """Gets the modification time for potentially long paths."""
    if os.name == 'nt' and not filepath.startswith('\\\\?\\'):
        filepath = '\\\\?\\' + os.path.abspath(filepath)
    try:
        return os.path.getmtime(filepath)
    except OSError as e:
        printl(f"Error getting modification time for '{filepath}': {e}")
        return None

def signal_handler(sig, frame):
    """Handles the SIGINT signal (CTRL+C)."""
    global exit_requested
    printl("\nCTRL+C pressed. Initiating clean exit...")
    exit_requested = True
    # You might want to set a flag for your worker threads to stop
    # their current processing gracefully if possible.

def get_file_size(filepath):
    """Gets the size of a file in bytes."""
    try:
        return os.path.getsize(filepath)
    except FileNotFoundError:
        return 0

def convert_bytes(num):
    """Converts bytes to human-readable format (IEC units)."""
    for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]:
        if abs(num) < 1024.0:
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} YB"

def load_and_clean_timestamps(timestamp_file, directory, recursive):
    """Loads timestamps, normalizes keys, removes duplicates and non-existent files in a single walk."""
    optimized_timestamps = {}
    existing_png_files = {}  # Use a dict to store {normalized_path: original_path}
    files_to_remove = []
    removed_count = 0

    try:
        with open(timestamp_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            optimized_timestamps = {
                unicodedata.normalize('NFC', key.replace(os.path.sep, '/')): value for key, value in data.items()
            }
    except FileNotFoundError:
        optimized_timestamps = {}
    except json.JSONDecodeError:
        printl(f"Warning: Corrupted timestamp file. Starting fresh.")
        optimized_timestamps = {}

    if recursive:
        walk_generator = os.walk(directory)
    else:
        walk_generator = [(directory, [], [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))])]

    for root, _, files in walk_generator:
        for file in files:
            if file.lower().endswith(".png"):
                original_path = os.path.join(root, file)
                relative_path = os.path.relpath(original_path, directory)
                normalized_path = unicodedata.normalize('NFC', relative_path.replace(os.path.sep, '/'))
                existing_png_files[normalized_path] = original_path

    timestamps_to_keep = {}
    for normalized_key, timestamp in optimized_timestamps.items():
        if normalized_key in existing_png_files:
            timestamps_to_keep[normalized_key] = timestamp
        else:
            removed_count += 1

    if removed_count > 0:
        printl(f"Removed {removed_count} timestamps for non-existent files.")
        try:
            with open(timestamp_file, 'w', encoding='utf-8') as f:
                json.dump(timestamps_to_keep, f)
        except IOError:
            printl(f"Error: Could not save updated timestamps in {timestamp_file}.")

    return timestamps_to_keep, existing_png_files

def add_optimized_timestamp(optimized_timestamps, filename, directory, timestamp):
    """Adds a processed file and a timestamp to the processed files set"""
    file_key = os.path.relpath(filename, directory)
    file_key = unicodedata.normalize('NFC', file_key.replace(os.path.sep, '/'))
    optimized_timestamps[file_key] = timestamp

def save_optimized_timestamps(optimized_timestamps, timestamp_file):
    """Saves the timestamp of a successfully optimized file with a lock."""
    try:
        with open(timestamp_file, 'w', encoding='utf-8') as f:
            json.dump(optimized_timestamps, f)
    except IOError:
        printl(f"Error: Could not save optimized timestamps in {timestamp_file}.")

def format_time(seconds):
    """Formats seconds into days:HH:MM:SS.ms, showing only relevant parts."""
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    seconds = int(seconds)
    minutes = seconds // 60
    seconds %= 60
    hours = minutes // 60
    minutes %= 60
    days = hours // 24
    hours %= 24

    parts = []
    if days > 0:
        parts.append(f"{days} days")
    if days > 0 or hours > 0:
        parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    else:
        parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    return f"{':'.join(parts)}.{milliseconds:03d}"

def optimize_png(progress_bar, original_filename, optipng_path="optipngp", optimization_level=5, worker_id=None, fix_errors=False):
    """Optimizes a single PNG file using optipng and reports progress, handling Unicode filenames."""
    worker_prefix = f"[Worker {worker_id}] " if worker_id is not None else ""
    temp_filename = None
    filename_to_process = original_filename
    posix_filename = ""
    if len(original_filename) > 255 and os.name == 'nt' and not original_filename.startswith('\\\\?\\'):
        posix_filename = '\\\\?\\' + os.path.abspath(original_filename)
        original_size = get_file_size(posix_filename)
    else:
        original_size = get_file_size(original_filename)
    renamed = False

    if has_unicode(original_filename) or len(original_filename) > 255:
        temp_filename = generate_temp_filename(original_filename)
        if posix_filename > "":
            original_filename = posix_filename
        try:
            os.rename(original_filename, temp_filename)
            filename_to_process = temp_filename
            renamed = True
            #progress_bar.write(f"{worker_prefix}Renamed '{original_filename}' to '{temp_filename}' for optipng processing.")
        except OSError as e:
            return [], False, f"Error renaming file '{original_filename}': {e}", 0

    try:
        command = [
            optipng_path,
            "-strip",
            "all",
            "-preserve",
            f"-o{optimization_level}",
        ]
        if fix_errors:
            command.append("-fix")
        command.append(filename_to_process)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout_bytes, stderr_bytes = process.communicate()

        try:
            stdout = stdout_bytes.decode('utf-8')
            stderr = stderr_bytes.decode('utf-8')
        except UnicodeDecodeError:
            stdout = stdout_bytes.decode(errors='replace')
            stderr = stderr_bytes.decode(errors='replace')

        if renamed and os.path.exists(temp_filename) and original_filename != temp_filename:
            try:
                os.rename(temp_filename, original_filename)
                #progress_bar.write(f"{worker_prefix}Renamed '{temp_filename}' back to '{original_filename}'.")
            except OSError as e:
                progress_bar.write(f"{worker_prefix}Error renaming '{temp_filename}' back to '{original_filename}': {e}")
                original_filename = temp_filename

        if process.returncode == 0:
            optimized_size = get_file_size(original_filename)
            savings = original_size - optimized_size
            return command, True, stdout.strip(), savings
        else:
            error_message = stderr.strip()
            return command, False, error_message, 0

    except FileNotFoundError:
        return [], False, f"Error: optipng command not found.", 0
    except Exception as e:
        return [], False, str(e), 0

def find_png_files(directory, recursive=True):
    """Finds all PNG files in the given directory."""
    png_files = []
    if recursive:
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(".png"):
                    png_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(directory):
            filepath = os.path.join(directory, file)
            if os.path.isfile(filepath) and file.lower().endswith(".png"):
                png_files.append(filepath)
    return png_files

def main():
    app_descr = "optipngdir - Optimize PNG files in a directory using optipng with multithreading and progress bar."
    parser = argparse.ArgumentParser(description=app_descr)
    parser.add_argument("directory", help="The directory containing the PNG files to optimize.")
    parser.add_argument("-t", "--threads", type=int, default=8, help="The number of threads to use for parallel optimization (default: 8).")
    parser.add_argument("-o", "--optimization-level", type=int, default=5, choices=range(0, 8), help="The optipng optimization level (0-7, default: 5).")
    parser.add_argument("-f", "--fix", action="store_true", help="Instruct OPTIPNG to fix PNG files.")
    parser.add_argument("--optipng-path", type=str, default="default", help="The path to the optipng executable if it's not in your system's PATH.")
    parser.add_argument("-R", "--recursive", action="store_true", help="Search for PNG files recursively in subdirectories.")

    args = parser.parse_args()
    directory = args.directory
    num_threads = args.threads
    optimization_level = args.optimization_level
    optipng_path = args.optipng_path
    recursive = args.recursive
    fix_errors = args.fix

    sys.stdout.write(enter_alt_screen)
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    printl(colored(app_descr, 'light_cyan'))

    if optipng_path == "default":
        operating_system = get_os()

        if operating_system == "Linux":
            printl("Running on Linux, using optipngp shell script to preserve timestamps.")
            optipng_path = "optipngp"
        elif operating_system == "macOS":
            printl("Running on MacOS, using optipngp shell script to preserve timestamps.")
            optipng_path = "optipngp"
        elif operating_system == "Windows":
            printl("Running on Windows, using optipng directly with the -preserve parameter.")
            optipng_path = "optipng"
        else:
            printl(f"Running on an unidentified operating system: {operating_system}")
            optipng_path = "optipng"

    if not os.path.isdir(directory):
        printl(f"Error: Directory '{directory}' not found.")
        return

    printl("Gathering files to be processed, please wait...")

    timestamp_file = os.path.join(directory, ".optimized_png_timestamps.json")
    optimized_timestamps, existing_png_files_map = load_and_clean_timestamps(timestamp_file, directory, recursive)
    files_to_optimize = []
    skipped_count = 0
    total_files_found = len(existing_png_files_map)

    signal.signal(signal.SIGINT, signal_handler)

    for normalized_path, original_path in existing_png_files_map.items():
        current_timestamp = get_modified_time_long_path(original_path)
        if normalized_path in optimized_timestamps and optimized_timestamps[normalized_path] == current_timestamp:
            skipped_count += 1
        else:
            files_to_optimize.append(original_path)

    printl(f"Found {total_files_found} PNG files{' recursively' if recursive else ''}.")
    printl(f"Skipping {skipped_count} already optimized files.")
    if not files_to_optimize:
        printl("No new files to optimize.")
        return

    printl(f"Optimizing {len(files_to_optimize)} new/modified PNG files using {num_threads} threads.")

    start_time = time.time()

    progress_bar = tqdm(total=len(files_to_optimize),
                        desc="",
                        bar_format=colored("", 'light_blue') + colored("{bar}", 'light_blue') + colored("", 'light_blue') +
                                           " {percentage:.2f}% {n_fmt}/{total_fmt} T {elapsed}/{remaining} {unit}",
                        unit="S "+convert_bytes(0),
                        dynamic_ncols=True)

    max_worker_threads = args.threads
    processed_count = 0
    successful_optimizations = 0
    total_savings_bytes = 0
    running_workers = 0
    worker_threads = []
    worker_progress = []
    worker_id_counter = 1
    error_count = 0

    def check_running():
        nonlocal worker_threads, worker_progress, running_workers
        # Check for finished threads and clean them up
        finished_threads = [t for t in worker_threads if not t.is_alive()]
        for finished_thread in finished_threads:
            finished_thread.join()  # Ensure thread has fully completed
            if hasattr(finished_thread, 'worker_id'):
                worker_progress = [w for w in worker_progress if w['id'] != finished_thread.worker_id]
            worker_threads.remove(finished_thread)
            running_workers -= 1
        return finished_threads

    def print_workers():
        global termlines
        nonlocal worker_progress, worker_id_counter, progress_bar
        #Print all running workers with file names:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        for termline in termlines:
            progress_bar.write(termline)
        for wi in worker_progress:
            wid_cols = len(str(worker_id_counter))
            msg = f"Worker {wi['id']:{wid_cols}d} processing: "
            swfn = shorten_filename(wi['fn'], get_terminal_width() - len(msg))
            progress_bar.write(msg+swfn)

    def worker(filename, worker_id):
        nonlocal files_to_optimize, processed_count, successful_optimizations, total_savings_bytes, progress_bar, directory, fix_errors, worker_progress, error_count
        global termlines, errlines

        if exit_requested:
            progress_bar.write(f"[Worker {worker_id}] Received exit signal. Terminating.")
            return
    
        if worker_id not in worker_progress:
            worker_progress.append({'id':worker_id,'fn':filename})

        command, success, output, savings = optimize_png(progress_bar, filename, optipng_path, optimization_level, worker_id, fix_errors)

        if not exit_requested:
            if success:
                successful_optimizations += 1
                total_savings_bytes += savings
                add_optimized_timestamp(optimized_timestamps, filename, directory, get_modified_time_long_path(filename))
            else:
                str_command = " ".join(command)
                to_log = colored(f"\n(x) ERROR TRYING TO PROCESS FILE!\n", 'light_red', attrs=['bold'])+colored(f"File name: {filename}\nWorker ID: {worker_id}\nCommand: {str_command}\nOPTIPNG terminal output:\n", 'light_cyan')+output+"\n"
                #progress_bar.write(to_log)
                errlines.append(to_log)
                error_count += 1
            processed_count += 1

            str_savings = ""
            if total_savings_bytes > 0:
                str_savings = colored("S "+convert_bytes(total_savings_bytes), 'light_magenta')
            else:
                str_savings = "S 0"

            if error_count > 0:
                progress_bar.unit=colored(f"E {error_count}", 'light_red')+" "+str_savings
            else:
                progress_bar.unit=str_savings

            time_finished = time.time()

            if processed_count > 0:
                avg_processing_time = (time_finished - start_time) / processed_count
                remaining_files = len(files_to_optimize) - processed_count
                estimated_remaining_time = remaining_files * avg_processing_time
                progress_bar.set_postfix(est_rem=tqdm.format_interval(estimated_remaining_time))
            else:
                progress_bar.set_postfix(est_rem="?")

            progress_bar.update(1)
        else:
            progress_bar.write(f"[Worker {worker_id}] Optimization of {os.path.basename(filename)} interrupted.")

    for filename in files_to_optimize:
        if exit_requested:
            break

        # Wait if we have reached the maximum number of running workers
        while running_workers >= max_worker_threads:
            # Check for finished threads and clean them up
            finished_threads = check_running()
            if len(finished_threads) > 0:
                print_workers()
            time.sleep(0.05)

        thread = threading.Thread(target=worker, args=(filename, worker_id_counter))
        thread.worker_id = worker_id_counter
        worker_threads.append(thread)
        thread.start()
        print_workers()

        #printl(f"[Worker {worker_id_counter}] Optimization of {os.path.basename(filename)} started.")
        running_workers += 1
        worker_id_counter += 1

    for thread in worker_threads:
        # Check for finished threads and clean them up
        _ = check_running()
        print_workers()
        thread.join()

    save_optimized_timestamps(optimized_timestamps, timestamp_file)

    progress_bar.close()
    end_time = time.time()

    sys.stdout.write(exit_alt_screen)
    sys.stdout.flush()

    elapsed_time = end_time - start_time

    #Print all errors
    for errln in errlines:
        print(errln)

    if len(files_to_optimize) > 0:
        print(f"\nOptimization complete.")
        print(f"Total files processed (new/modified): {len(files_to_optimize)}")
        print(f"Successfully optimized: {successful_optimizations}")
        print(f"Total savings: {convert_bytes(total_savings_bytes)}")
        print(f"Time taken: {format_time(elapsed_time)}")
    else:
        print("\nNo new files were optimized.")

if __name__ == "__main__":
    main()
