from flask import Flask, request, jsonify
from flask_cors import CORS  # ✅ Enable cross-origin requests for WordPress
import cloudscraper
from bs4 import BeautifulSoup
import re
import traceback  # ✅ For better error logging

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)  # ✅ Fixes "Failed to Fetch" on WordPress

# User-Agent rotation to avoid bot detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0"
]

# Base URLs
MAIN_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars"
GENRE_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

# Initialize Cloudscraper
scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'desktop': True})

def extract_book_id(book_url):
    """Extracts the book ID from a Royal Road book URL."""
    match = re.search(r'/fiction/(\d+)/', book_url)
    return match.group(1) if match else None

def get_tags_and_id(book_url):
    """Extracts book ID and tags from a Royal Road book page."""
    try:
        headers = {"User-Agent": USER_AGENTS[0]}
        response = scraper.get(book_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract book ID from URL
        book_id = extract_book_id(book_url)

        # Extract tags from the book's page
        tags = []
        for tag in soup.find_all("a", class_="fiction-tag"):
            tag_url = tag["href"]
            if "tagsAdd=" in tag_url:
                tag_name = tag_url.split("tagsAdd=")[-1]
                tags.append(tag_name)

        return book_id, tags

    except Exception as e:
        print("ERROR in get_tags_and_id:", e)
        traceback.print_exc()
        return None, None

def check_rising_stars(book_id, tags):
    """Checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}

    # Check the Main Rising Stars list first
    try:
        headers = {"User-Agent": USER_AGENTS[0]}
        response = scraper.get(MAIN_RISING_STARS_URL, headers=headers, timeout=10)
        
        print("Main Rising Stars Response Status:", response.status_code)  # ✅ Debugging
        print("Main Rising Stars Response Content:", response.text[:500])  # ✅ First 500 chars

        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Find all book links in the main Rising Stars list
        book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

        # Check if the book is present
        if book_id in book_ids:
            position = book_ids.index(book_id) + 1
            results["Main Rising Stars"] = f"✅ Found in position #{position}"
        else:
            results["Main Rising Stars"] = "❌ Not found in Main Rising Stars list"

    except Exception as e:
        print("ERROR in check_rising_stars (Main List):", e)
        traceback.print_exc()
        results["Main Rising Stars"] = f"⚠️ Failed to check: {e}"

    # Check each genre's Rising Stars page
    for tag in tags:
        url = f"{GENRE_RISING_STARS_URL}{tag}"
        try:
            response = scraper.get(url, headers=headers, timeout=10)
            
            print(f"Checking {tag} Rising Stars - Response Status:", response.status_code)  # ✅ Debugging
            print(f"Checking {tag} Rising Stars - Response Content:", response.text[:500])  # ✅ First 500 chars

            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Find all book links in the genre's Rising Stars list
            book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

            # Check if the book is present
            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                results[tag] = f"✅ Found in position #{position}"
            else:
                results[tag] = f"❌ Not found in '{tag}' Rising Stars list"

        except Exception as e:
            print(f"ERROR in check_rising_stars ({tag} List):", e)
            traceback.print_exc()
            results[tag] = f"⚠️ Failed to check: {e}"

    return results

@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    try:
        book_url = request.args.get("book_url")

        # Validate the URL
        if not book_url or "royalroad.com" not in book_url:
            return jsonify({"error": "Invalid Royal Road URL"}), 400

        print(f"Received request for book URL: {book_url}")  # ✅ Debugging

        # Fetch book ID and tags
        book_id, tags = get_tags_and_id(book_url)

        if book_id and tags:
            results = check_rising_stars(book_id, tags)
            return jsonify(results)
        else:
            print("ERROR: Failed to retrieve book details")
            return jsonify({"error": "Failed to retrieve book details"}), 500

    except Exception as e:
        print("Unexpected error:", e)
        traceback.print_exc()
        return jsonify({"error": f"Unexpected server error: {str(e)}"}), 500

if __name__ == '__main__':
    import os
    PORT = int(os.environ.get("PORT", 10000))  # ✅ Uses PORT=10000 (fixes Render deployment issues)
    app.run(host="0.0.0.0", port=PORT, debug=True)
