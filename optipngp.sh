#!/bin/bash

# Script to run optipng and preserve timestamps

# Get the total number of arguments
num_args=$#

# Check if at least one argument (the filename) was provided
if [ "$num_args" -lt 1 ]; then
  echo "Usage: $0 [optipng options...] <filename>"
  exit 1
fi

# Extract the filename (the last argument)
filename="${!num_args}"

# Remove the last argument from the list of positional parameters
# This leaves $@ containing only the optipng options
shift $((num_args - 1))

# Check if the file exists
if [ ! -f "$filename" ]; then
  echo "Error: File '$filename' not found."
  exit 1
fi

# Get original modification and access times
original_mtime=$(date -r "$filename" +%s)
original_atime=$(stat -c %X "$filename") # Access time might not always be reliable over CIFS

# Run optipng, passing all remaining arguments and the filename
if ! optipng "$@"; then
  echo "Error: optipng failed to process '$filename'."
  exit 1
fi

# Restore timestamps
if [ -n "$original_mtime" ]; then
  touch -d "@$original_mtime" "$filename"
fi
# Restoring access time might require 'utime' and might not always work reliably over CIFS
# if [ -n "$original_atime" ]; then
#   utime -t "$original_atime" "$filename"
# fi

echo "Processed '$filename'."
exit 0
