from flask import Flask, request, jsonify
import cloudscraper
from bs4 import BeautifulSoup

# Flask app (only needed for Render deployment)
app = Flask(__name__)

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

# Initialize Cloudscraper with Cloudflare Challenge Mode
scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'desktop': True})

# Function to check Rising Stars rankings
def check_rising_stars():
    results = {}

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

            # Debugging: Print first 500 characters of HTML response
            print(f"HTML response for {tag}:\n{response.text[:500]}\n")

            # Parse the HTML
            soup = BeautifulSoup(response.text, "html.parser")

            # Find all highlighted book titles on the Rising Stars list
            titles = [a.text.strip() for a in soup.find_all('a', class_='font-red-sunglo bold')]

            # Normalize extracted titles (lowercase, stripped)
            normalized_titles = [t.lower().strip() for t in titles]

            # Normalize book name for comparison
            normalized_book_name = book_name.lower().strip()

            if normalized_book_name in normalized_titles:
                position = normalized_titles.index(normalized_book_name) + 1
                results[tag] = f"✅ Found in position #{position}"
                print(f"✅ Found '{book_name}' in '{tag}' Rising Stars list at position #{position}.")
            else:
                results[tag] = "❌ Not found in this category"
                print(f"❌ '{book_name}' not found in '{tag}' Rising Stars list.")

        except Exception as e:
            results[tag] = f"⚠️ Failed to check: {e}"
            print(f"⚠️ Failed to check '{tag}': {e}")

    return results

# Flask route for API calls
@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    results = check_rising_stars()
    return jsonify(results)

# Run the Flask app (only needed for Render deployment)
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)
