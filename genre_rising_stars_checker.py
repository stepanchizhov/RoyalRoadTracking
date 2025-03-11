from flask import Flask, request, jsonify
from flask_cors import CORS  # ✅ Added to enable cross-origin requests
import cloudscraper
from bs4 import BeautifulSoup
import re

app = Flask(__name__)
CORS(app)  # ✅ Enables CORS for all routes (fixes "Failed to Fetch" on WordPress)

# Base URL for Rising Stars
BASE_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

# Initialize Cloudscraper with Cloudflare Challenge Mode
scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'desktop': True})

def extract_book_id(book_url):
    """Extracts the book ID from a Royal Road book URL."""
    match = re.search(r'/fiction/(\d+)/', book_url)
    return match.group(1) if match else None

def get_tags_from_book(url):
    """Extracts tags from a Royal Road book page."""
    try:
        response = scraper.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract tags from the book's page
        tags = []
        for tag in soup.find_all("a", class_="fiction-tag"):
            tag_url = tag["href"]
            if "tagsAdd=" in tag_url:
                tag_name = tag_url.split("tagsAdd=")[-1]
                tags.append(tag_name)

        return tags

    except Exception as e:
        return None

def check_rising_stars(book_id, tags):
    """Checks if the book is listed in Rising Stars under relevant tags using book ID."""
    results = {}

    for tag in tags:
        url = f"{BASE_URL}{tag}"
        try:
            response = scraper.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Find all book links on the Rising Stars list
            book_links = [a["href"] for a in soup.find_all('a', class_='font-red-sunglo bold', href=True)]

            # Check if the book ID is present in the list
            if any(f"/fiction/{book_id}/" in link for link in book_links):
                position = next(i + 1 for i, link in enumerate(book_links) if f"/fiction/{book_id}/" in link)
                results[tag] = f"✅ Found in position #{position}"
            else:
                results[tag] = "❌ Not found in this category"

        except Exception as e:
            results[tag] = f"⚠️ Failed to check: {e}"

    return results

@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    book_url = request.args.get("book_url")

    # Validate the URL
    if not book_url or "royalroad.com" not in book_url:
        return jsonify({"error": "Invalid Royal Road URL"}), 400

    # Extract the book ID
    book_id = extract_book_id(book_url)
    if not book_id:
        return jsonify({"error": "Invalid book ID"}), 400

    # Fetch book tags
    tags = get_tags_from_book(book_url)
    if tags:
        results = check_rising_stars(book_id, tags)
        return jsonify(results)
    else:
        return jsonify({"error": "Failed to retrieve book details"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
