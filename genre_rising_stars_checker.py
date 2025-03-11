from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

# Book title to search for
book_name = "The Dark Lady’s Guide to Villainy"

# Tags to check
tags = [
    "ruling_class",
    "comedy",
    "female_lead",
    "adventure",
    "fantasy",
    "romance",
    "attractive_lead",
    "gender_bender",
    "high_fantasy",
    "low_fantasy",
    "magic",
    "school_life"
]

# Base URL
base_url = "https://www.royalroad.com/fictions/rising-stars?genre="

# User-Agent to avoid being blocked
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
}

# Check each tag's Rising Stars page
for tag in tags:
    url = f"{base_url}{tag}"
    try:
        print(f"Checking {tag}...")
        response = requests.get(url, headers=HEADERS, timeout=10)
        print(f"HTML response for {tag}: {response.text[:500]}")  # Print first 500 characters

        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Find all highlighted book titles on the Rising Stars list
        titles = [a.text.strip() for a in soup.find_all('a', class_='font-red-sunglo bold')]

        # Check if the book is present and find its ranking position
        if book_name in titles:
            position = titles.index(book_name) + 1  # Convert index to human-readable position
            print(f"✅ Found '{book_name}' in '{tag}' Rising Stars list at position #{position}.")
        else:
            print(f"❌ '{book_name}' not found in '{tag}' Rising Stars list.")

    except requests.RequestException as e:
        print(f"⚠️ Failed to check '{tag}': {e}")

print("\nCheck complete.")

