from flask import Flask, request, jsonify
from flask_cors import CORS
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import random

# Enable logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

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
    match = re.search(r'/fiction/(\d+)', book_url)
    return match.group(1) if match else None

def get_book_details(book_url):
    """Extracts book ID, title, categories (names), and tags (URLs) from a Royal Road book page."""
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        logging.info(f"Fetching book page: {book_url} with User-Agent: {headers['User-Agent']}")

        response = scraper.get(book_url, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.error(f"❌ Failed to fetch book page, Status Code: {response.status_code}")
            return None, None, None, None

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract book ID
        book_id = extract_book_id(book_url)
        if not book_id:
            logging.error("❌ Failed to extract book ID from URL")
            return None, None, None, None

        # Extract book title
        title_tag = soup.find("h1", class_="font-white")
        book_title = title_tag.text.strip() if title_tag else "Unknown Title"

        # Extract category names and corresponding tags
        categories = {}  # { tag: category_name }
        for tag in soup.find_all("a", class_="fiction-tag"):
            tag_url = tag["href"]
            if "tagsAdd=" in tag_url:
                tag_name = tag_url.split("tagsAdd=")[-1]  # The tag used in URLs
                category_name = tag.text.strip()  # The human-readable category
                categories[tag_name] = category_name

        logging.info(f"✅ Extracted book details: ID={book_id}, Title='{book_title}', Categories={categories}")
        return book_id, book_title, categories

    except Exception as e:
        logging.exception("❌ Error fetching book details")
        return None, None, None

def check_rising_stars(book_id, categories):
    """Checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}

    # Check the Main Rising Stars list first
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        logging.info("Checking Main Rising Stars list...")

        response = scraper.get(MAIN_RISING_STARS_URL, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

        if book_id in book_ids:
            position = book_ids.index(book_id) + 1
            results["Main Rising Stars"] = f"✅ Found in position #{position}"
            logging.info(f"✅ Book {book_id} found in Main Rising Stars at position {position}")
        else:
            results["Main Rising Stars"] = "❌ Not found in Main Rising Stars list"
            logging.info(f"❌ Book {book_id} not found in Main Rising Stars")

    except Exception as e:
        logging.exception("⚠️ Failed to check Main Rising Stars")
        results["Main Rising Stars"] = f"⚠️ Failed to check: {e}"

    # Check each **tag-based** Rising Stars page
    for tag, category_name in categories.items():
        url = f"{GENRE_RISING_STARS_URL}{tag}"
        try:
            logging.info(f"Checking Rising Stars for tag: {tag} ({category_name})")
            response = scraper.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                results[category_name] = f"✅ Found in position #{position}"
                logging.info(f"✅ Book {book_id} found in {category_name} at position {position}")
            else:
                results[category_name] = f"❌ Not found in '{category_name}' Rising Stars list"
                logging.info(f"❌ Book {book_id} not found in {category_name}")

        except Exception as e:
            logging.exception(f"⚠️ Failed to check {category_name} Rising Stars")
            results[category_name] = f"⚠️ Failed to check: {e}"

    return results

@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    book_url = request.args.get("book_url")

    logging.info(f"Received request for book URL: {book_url}")

    if not book_url or "royalroad.com" not in book_url:
        logging.error("❌ Invalid Royal Road URL")
        return jsonify({"error": "Invalid Royal Road URL"}), 400

    book_id, book_title, categories = get_book_details(book_url)

    if book_id and categories:
        results = check_rising_stars(book_id, categories)
        return jsonify({
            "book_title": book_title,
            "categories": list(categories.values()),  # Only display readable category names
            "results": results
        })
    else:
        logging.error("❌ Failed to retrieve book details")
        return jsonify({"error": "Failed to retrieve book details"}), 500

if __name__ == '__main__':
    import os
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT, debug=True)
