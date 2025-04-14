import subprocess
import os
import sys
import platform
import signal
import unicodedata
import argparse
import threading
import time
import json
from termcolor import colored
from tqdm import tqdm

# Global flag to indicate if a clean exit has been requested
exit_requested = False

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

def signal_handler(sig, frame):
    """Handles the SIGINT signal (CTRL+C)."""
    global exit_requested
    print("\nCTRL+C pressed. Initiating clean exit...")
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
                unicodedata.normalize('NFC', key.replace(os.path.sep, '/')): value
                for key, value in data.items()
            }
    except FileNotFoundError:
        optimized_timestamps = {}
    except json.JSONDecodeError:
        print(f"Warning: Corrupted timestamp file. Starting fresh.")
        optimized_timestamps = {}

    if recursive:
        walk_generator = os.walk(directory)
    else:
        walk_generator = [(directory, [], [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))])]

    for root, _, files in walk_generator:
        for file in files:
            if file.lower().endswith(".png"):
                original_path = os.path.join(root, file)
                normalized_path = unicodedata.normalize('NFC', original_path.replace(os.path.sep, '/'))
                existing_png_files[normalized_path] = original_path

    timestamps_to_keep = {}
    for normalized_key, timestamp in optimized_timestamps.items():
        if normalized_key in existing_png_files:
            timestamps_to_keep[normalized_key] = timestamp
        else:
            removed_count += 1

    if removed_count > 0:
        print(f"Removed {removed_count} timestamps for non-existent files.")
        try:
            with open(timestamp_file, 'w', encoding='utf-8') as f:
                json.dump(timestamps_to_keep, f)
        except IOError:
            print(f"Error: Could not save updated timestamps in {timestamp_file}.")

    return timestamps_to_keep, existing_png_files

def add_optimized_timestamp(optimized_timestamps, filename, timestamp):
    """Adds a processed file and a timestamp to the processed files set"""
    file_key = unicodedata.normalize('NFC', filename.replace(os.path.sep, '/'))
    optimized_timestamps[file_key] = timestamp

def save_optimized_timestamps(optimized_timestamps, timestamp_file):
    """Saves the timestamp of a successfully optimized file with a lock."""
    try:
        with open(timestamp_file, 'w', encoding='utf-8') as f:
            json.dump(optimized_timestamps, f)
    except IOError:
        print(f"Error: Could not save optimized timestamps in {timestamp_file}.")

def optimize_png(filename, optipng_path="optipngp", optimization_level=5, worker_id=None, progress_dict=None):
    """Optimizes a single PNG file using optipng and reports progress."""
    worker_prefix = f"[Worker {worker_id}] " if worker_id is not None else ""
    original_size = get_file_size(filename)
    try:
        command = [
            optipng_path,
            "-strip",
            "all",
            "-preserve",
            f"-o{optimization_level}",
            filename,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # Don't decode here

        stdout_bytes, stderr_bytes = process.communicate()

        try:
            stdout = stdout_bytes.decode('utf-8')
            stderr = stderr_bytes.decode('utf-8')
        except UnicodeDecodeError:
            stdout = stdout_bytes.decode(errors='replace')  # Replace undecodable characters
            stderr = stderr_bytes.decode(errors='replace')

        for line in stdout.splitlines():
            if progress_dict is not None and filename in progress_dict:
                progress_dict[filename]['worker_output'] = line.strip()

        stdout, stderr = process.communicate()

        if process.returncode == 0:
            optimized_size = get_file_size(filename)
            savings = original_size - optimized_size
            return command, True, stdout, savings
        else:
            return command, False, stderr, 0

    except FileNotFoundError:
        return False, f"Error: optipng command not found.", 0
    except Exception as e:
        return False, str(e), 0

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
    parser = argparse.ArgumentParser(description="Optimize PNG files in a directory using optipng with multithreading and progress bar.")
    parser.add_argument("directory", help="The directory containing the PNG files to optimize.")
    parser.add_argument("-t", "--threads", type=int, default=8, help="The number of threads to use for parallel optimization (default: 8).")
    parser.add_argument("-o", "--optimization-level", type=int, default=5, choices=range(0, 8), help="The optipng optimization level (0-7, default: 5).")
    parser.add_argument("--optipng-path", type=str, default="default", help="The path to the optipng executable if it's not in your system's PATH.")
    parser.add_argument("-R", "--recursive", action="store_true", help="Search for PNG files recursively in subdirectories.")
    parser.add_argument("--smoothing", type=float, default=0.8, help="Smoothing factor for tqdm's ETA (0-1, higher values are smoother).")
    parser.add_argument("--mininterval", type=float, default=1, help="Minimum update interval for tqdm's progress bar (in seconds).")

    args = parser.parse_args()
    directory = args.directory
    num_threads = args.threads
    optimization_level = args.optimization_level
    optipng_path = args.optipng_path
    recursive = args.recursive
    smoothing = args.smoothing
    mininterval = args.mininterval

    if optipng_path == "default":
        operating_system = get_os()

        if operating_system == "Linux":
            print("Running on Linux, using optipngp shell script to preserve timestamps.")
            optipng_path = "optipngp"
        elif operating_system == "macOS":
            print("Running on MacOS, using optipngp shell script to preserve timestamps.")
            optipng_path = "optipngp"
        elif operating_system == "Windows":
            print("Running on Windows, using optipng directly with the -preserve parameter.")
            optipng_path = "optipng"
        else:
            print(f"Running on an unidentified operating system: {operating_system}")
            optipng_path = "optipng"

    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' not found.")
        return

    timestamp_file = os.path.join(directory, ".optimized_png_timestamps.json")
    optimized_timestamps, existing_png_files_map = load_and_clean_timestamps(timestamp_file, directory, recursive)
    files_to_optimize = []
    skipped_count = 0
    total_files_found = len(existing_png_files_map)

    signal.signal(signal.SIGINT, signal_handler)

    for normalized_path, original_path in existing_png_files_map.items():
        current_timestamp = os.path.getmtime(original_path)
        if normalized_path in optimized_timestamps and optimized_timestamps[normalized_path] == current_timestamp:
            skipped_count += 1
        else:
            files_to_optimize.append(original_path)

    print(f"Found {total_files_found} PNG files.")
    print(f"Skipping {skipped_count} already optimized files.")
    if not files_to_optimize:
        print("No new files to optimize.")
        return

    print(f"Optimizing {len(files_to_optimize)} new/modified PNG files using {num_threads} threads.")

    progress_bar = tqdm(total=len(files_to_optimize),
                        desc="",
                        bar_format=colored("", 'light_blue') + colored("{bar}", 'light_blue') + colored("", 'light_blue') +
                                           " {percentage:.2f}% {n_fmt}/{total_fmt} T {elapsed}/{remaining} {unit}",
                        unit="S "+convert_bytes(0),
                        dynamic_ncols=True,
                        smoothing=smoothing,
                        mininterval=mininterval)
    start_time = time.time()
    processed_count = 0
    successful_optimizations = 0
    total_savings_bytes = 0
    worker_progress = {}
    threads = []
    worker_id_counter = 1

    def worker(filename, worker_id):
        nonlocal processed_count, successful_optimizations, total_savings_bytes, progress_bar

        if exit_requested:
            progress_bar.write(f"[Worker {worker_id}] Received exit signal. Terminating.")
            return

        worker_progress[filename] = {'status': 'Processing', 'output': ''}
        command, success, output, savings = optimize_png(filename, optipng_path, optimization_level, worker_id, worker_progress)
        str_command = " ".join(command)
        if not exit_requested:
            if success:
                successful_optimizations += 1
                total_savings_bytes += savings
                worker_progress[filename]['status'] = 'Done'
                add_optimized_timestamp(optimized_timestamps, filename, os.path.getmtime(filename))
            else:
                worker_progress[filename]['status'] = 'Error'
                worker_progress[filename]['output'] = output.strip().decode('utf-8', errors='replace')
                progress_bar.write(colored(f"\n(x) ERROR TRYING TO PROCESS FILE!\n", 'light_red', attrs=['bold']))
                progress_bar.write(colored(f"File name: {filename}\nWorker ID: {worker_id}\nCommand: {str_command}\nOPTIPNG terminal output:\n", 'light_cyan'))
                progress_bar.write(worker_progress[filename]['output']+"\n")
            processed_count += 1
            progress_bar.unit="S "+convert_bytes(total_savings_bytes)
            progress_bar.update(1)
        else:
            progress_bar.write(f"[Worker {worker_id}] Optimization of {os.path.basename(filename)} interrupted.")

    max_worker_threads = args.threads
    running_workers = 0
    worker_threads = []
    worker_id_counter = 1

    for filename in files_to_optimize:
        if exit_requested:
            break

        # Wait if we have reached the maximum number of running workers
        while running_workers >= max_worker_threads:
            time.sleep(0.05)
            # Check for finished threads and clean them up
            worker_threads = [t for t in worker_threads if t.is_alive()]
            running_workers = len(worker_threads)

        thread = threading.Thread(target=worker, args=(filename, worker_id_counter))
        worker_threads.append(thread)
        thread.start()
        #print(f"[Worker {worker_id_counter}] Optimization of {os.path.basename(filename)} started.")
        running_workers += 1
        worker_id_counter += 1

    for thread in worker_threads:
        thread.join()

    save_optimized_timestamps(optimized_timestamps, timestamp_file)

    progress_bar.close()
    end_time = time.time()
    elapsed_time = end_time - start_time

    if len(files_to_optimize) > 0:
        print(f"\nOptimization complete.")
        print(f"Total files processed (new/modified): {len(files_to_optimize)}")
        print(f"Successfully optimized: {successful_optimizations}")
        print(f"Total savings: {convert_bytes(total_savings_bytes)}")
        print(f"Time taken: {elapsed_time:.2f} seconds")
    else:
        print("\nNo new files were optimized.")

if __name__ == "__main__":
    main()
