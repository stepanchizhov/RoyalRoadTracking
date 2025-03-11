from flask import Flask, request, jsonify
import cloudscraper
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

# User-Agent rotation to avoid bot detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0"
]

# Initialize Cloudscraper
scraper = cloudscraper.create_scraper()

# Check each tag's Rising Stars page
for tag in tags:
    url = f"{base_url}{tag}"
    try:
        print(f"Checking {tag}...")

        headers = {
            "User-Agent": USER_AGENTS[0]  # Using the first User-Agent from the list
        }

        # Request the Rising Stars page with Cloudscraper
        response = scraper.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse the HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Find all highlighted book titles on the Rising Stars list
        titles = [a.text.strip() for a in soup.find_all('a', class_='font-red-sunglo bold')]

        # Check if the book is present and find its ranking position
        if book_name in titles:
            position = titles.index(book_name) + 1  # Convert index to human-readable position
            print(f"✅ Found '{book_name}' in '{tag}' Rising Stars list at position #{position}.")
        else:
            print(f"❌ '{book_name}' not found in '{tag}' Rising Stars list.")

    except Exception as e:
        print(f"⚠️ Failed to check '{tag}': {e}")

print("\nCheck complete.")


