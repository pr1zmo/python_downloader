#!/usr/bin/env python3
"""
Script to find duplicate files in a directory and all its subdirectories.
Uses file content hashing (MD5) to identify duplicates.
"""

import os
import hashlib
from collections import defaultdict
import sys


def get_file_hash(filepath, block_size=65536):
    """
    Calculate MD5 hash of a file.
    
    Args:
        filepath: Path to the file
        block_size: Size of blocks to read at a time (default 64KB)
    
    Returns:
        MD5 hash as hexadecimal string
    """
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(block_size)
                if not data:
                    break
                hasher.update(data)
        return hasher.hexdigest()
    except (IOError, OSError) as e:
        print(f"Error reading file {filepath}: {e}", file=sys.stderr)
        return None


def find_duplicates(directory):
    """
    Find all duplicate files in the given directory and subdirectories.
    
    Args:
        directory: Root directory to search
    
    Returns:
        Dictionary mapping hash to list of file paths with that hash
    """
    hash_map = defaultdict(list)
    
    # Walk through all directories and subdirectories
    for root, dirs, files in os.walk(directory):
        for filename in files:
            filepath = os.path.join(root, filename)
            
            # Skip symbolic links to avoid infinite loops
            if os.path.islink(filepath):
                continue
            
            # Calculate hash and store filepath
            file_hash = get_file_hash(filepath)
            if file_hash:
                hash_map[file_hash].append(filepath)
    
    # Filter to only keep hashes with duplicates (more than 1 file)
    duplicates = {hash_val: paths for hash_val, paths in hash_map.items() 
                  if len(paths) > 1}
    
    return duplicates


def print_duplicates(duplicates):
    """
    Print duplicate files in a readable format.
    
    Args:
        duplicates: Dictionary mapping hash to list of duplicate file paths
    """
    if not duplicates:
        print("No duplicate files found.")
        return
    
    total_groups = len(duplicates)
    total_files = sum(len(paths) for paths in duplicates.values())
    
    print(f"\nFound {total_groups} group(s) of duplicate files:")
    print(f"Total duplicate files: {total_files}\n")
    print("=" * 80)
    
    for idx, (file_hash, paths) in enumerate(duplicates.items(), 1):
        print(f"\nDuplicate Group {idx} (Hash: {file_hash}):")
        print(f"  Files with identical content ({len(paths)} files):")
        for path in sorted(paths):
            file_size = os.path.getsize(path)
            print(f"    - {path} ({file_size:,} bytes)")
        print("-" * 80)


def main():
    """Main function to run the duplicate finder."""
    if len(sys.argv) != 2:
        print("Usage: python find_duplicates.py <directory_path>")
        print("\nExample:")
        print("  python find_duplicates.py /path/to/search")
        sys.exit(1)
    
    directory = sys.argv[1]
    
    # Validate directory
    if not os.path.exists(directory):
        print(f"Error: Directory '{directory}' does not exist.")
        sys.exit(1)
    
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a directory.")
        sys.exit(1)
    
    print(f"Searching for duplicate files in: {directory}")
    print("Please wait, this may take a while for large directories...\n")
    
    # Find and display duplicates
    duplicates = find_duplicates(directory)
    print_duplicates(duplicates)


if __name__ == "__main__":
    main()