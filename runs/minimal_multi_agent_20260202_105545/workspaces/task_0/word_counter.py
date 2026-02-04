#!/usr/bin/env python3
"""
Word Frequency Counter
A Python program that reads a text file, counts word frequencies,
sorts results, saves to file, and displays top N words.
"""

import re
import sys


def read_or_create_file(filepath):
    """
    Read content from a text file. If file doesn't exist, create it with sample text.
    
    Args:
        filepath (str): Path to the input text file
        
    Returns:
        str: Content of the file as a string
        
    Raises:
        IOError: If there are permission issues or other I/O errors
    """
    try:
        # Try to open and read the file
        with open(filepath, 'r', encoding='utf-8') as file:
            content = file.read()
            print(f"Successfully read file: {filepath}")
            return content
    except FileNotFoundError:
        # Create sample file if it doesn't exist
        print(f"File '{filepath}' not found. Creating sample file...")
        sample_text = """This is a sample text file created automatically.
It contains various words for testing word frequency counting.
The quick brown fox jumps over the lazy dog.
Python is a powerful programming language.
Word counting is a common text processing task.
This file will be used for demonstration purposes."""
        
        try:
            with open(filepath, 'w', encoding='utf-8') as file:
                file.write(sample_text)
            print(f"Sample file created: {filepath}")
            
            # Read the newly created file
            with open(filepath, 'r', encoding='utf-8') as file:
                return file.read()
        except IOError as e:
            print(f"Error creating file: {e}")
            sys.exit(1)
    except IOError as e:
        print(f"Error reading file '{filepath}': {e}")
        sys.exit(1)


def count_words(text):
    """
    Count the frequency of each word in the text.
    
    Args:
        text (str): Input text string
        
    Returns:
        dict: Dictionary with words as keys and counts as values
    """
    # Convert to lowercase for case-insensitive counting
    lower_text = text.lower()
    
    # Use regex to extract words (only alphabetic characters)
    # \b matches word boundaries, [a-z]+ matches one or more letters
    words = re.findall(r'\b[a-z]+\b', lower_text)
    
    # Count word frequencies
    word_count = {}
    for word in words:
        word_count[word] = word_count.get(word, 0) + 1
    
    print(f"Total words found: {len(words)}")
    print(f"Unique words: {len(word_count)}")
    return word_count


def sort_word_counts(word_dict):
    """
    Sort word counts in descending order of frequency.
    
    Args:
        word_dict (dict): Dictionary of word counts
        
    Returns:
        list: List of (word, count) tuples sorted by count descending
    """
    # Sort by count descending, then by word ascending for ties
    sorted_items = sorted(word_dict.items(), 
                         key=lambda item: (-item[1], item[0]))
    return sorted_items


def save_results(sorted_list, output_filepath):
    """
    Save sorted word counts to a text file.
    
    Args:
        sorted_list (list): List of (word, count) tuples
        output_filepath (str): Path to output file
        
    Raises:
        IOError: If there are issues writing to the file
    """
    try:
        with open(output_filepath, 'w', encoding='utf-8') as file:
            file.write("Word Frequency Results\n")
            file.write("=" * 25 + "\n")
            file.write(f"Total unique words: {len(sorted_list)}\n\n")
            
            for word, count in sorted_list:
                file.write(f"{word}: {count}\n")
        
        print(f"Results saved to: {output_filepath}")
    except IOError as e:
        print(f"Error writing to file '{output_filepath}': {e}")
        sys.exit(1)


def print_top_n(sorted_list, n=10):
    """
    Print the top N most frequent words.
    
    Args:
        sorted_list (list): List of (word, count) tuples
        n (int): Number of top words to display
    """
    if not sorted_list:
        print("No words found in the text.")
        return
    
    print(f"\nTop {min(n, len(sorted_list))} most frequent words:")
    print("-" * 30)
    
    for i, (word, count) in enumerate(sorted_list[:n], 1):
        print(f"{i:2}. {word:15} : {count:3}")
    
    # Show total word count if available
    total_words = sum(count for _, count in sorted_list)
    print(f"\nTotal words processed: {total_words}")


def main():
    """
    Main function to coordinate the word counting process.
    """
    try:
        # Configuration
        input_file = "input.txt"
        output_file = "word_count.txt"
        top_n = 10
        
        print("=" * 50)
        print("Word Frequency Counter")
        print("=" * 50)
        
        # Step 1: Read or create input file
        text = read_or_create_file(input_file)
        
        if not text.strip():
            print("Warning: Input file is empty.")
            # Still proceed but results will be empty
        
        # Step 2: Count words
        word_counts = count_words(text)
        
        # Step 3: Sort results
        sorted_words = sort_word_counts(word_counts)
        
        # Step 4: Save results to file
        save_results(sorted_words, output_file)
        
        # Step 5: Display top N words
        print_top_n(sorted_words, top_n)
        
        print("\n" + "=" * 50)
        print("Word counting completed successfully!")
        print("=" * 50)
        
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        print("Program terminated due to an error.")
        sys.exit(1)


if __name__ == "__main__":
    main()