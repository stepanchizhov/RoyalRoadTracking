from flask import Flask, request, jsonify
from flask_cors import CORS  # ✅ Enable cross-origin requests for WordPress
import cloudscraper
from bs4 import BeautifulSoup
import re
import asyncio
import aiohttp

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # ✅ Fixes "Failed to Fetch" on WordPress

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

async def fetch_page(session, url):
    """Fetches a webpage asynchronously."""
    try:
        headers = {"User-Agent": USER_AGENTS[0]}
        async with session.get(url, headers=headers, timeout=10) as response:
            return await response.text()
    except Exception as e:
        return str(e)

async def get_tags_and_id(book_url):
    """Extracts book ID and tags from a Royal Road book page."""
    try:
        book_id = extract_book_id(book_url)
        async with aiohttp.ClientSession() as session:
            page_content = await fetch_page(session, book_url)
            soup = BeautifulSoup(page_content, "html.parser")

            tags = []
            for tag in soup.find_all("a", class_="fiction-tag"):
                tag_url = tag["href"]
                if "tagsAdd=" in tag_url:
                    tag_name = tag_url.split("tagsAdd=")[-1]
                    tags.append(tag_name)

            return book_id, tags

    except Exception as e:
        return None, None

async def check_rising_stars(book_id, tags):
    """Checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}

    # Check the Main Rising Stars list first
    try:
        async with aiohttp.ClientSession() as session:
            main_page_content = await fetch_page(session, MAIN_RISING_STARS_URL)
            soup = BeautifulSoup(main_page_content, "html.parser")

            book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                results["Main Rising Stars"] = f"✅ Found in position #{position}"
            else:
                results["Main Rising Stars"] = "❌ Not found in Main Rising Stars list"

    except Exception as e:
        results["Main Rising Stars"] = f"⚠️ Failed to check: {e}"

    # Check each genre's Rising Stars page
    async with aiohttp.ClientSession() as session:
        for tag in tags:
            url = f"{GENRE_RISING_STARS_URL}{tag}"
            try:
                genre_page_content = await fetch_page(session, url)
                soup = BeautifulSoup(genre_page_content, "html.parser")

                book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

                if book_id in book_ids:
                    position = book_ids.index(book_id) + 1
                    results[tag] = f"✅ Found in position #{position}"
                else:
                    results[tag] = f"❌ Not found in '{tag}' Rising Stars list"

            except Exception as e:
                results[tag] = f"⚠️ Failed to check: {e}"

    return results

@app.route('/check_rising_stars', methods=['GET'])
async def api_rising_stars():
    book_url = request.args.get("book_url")

    if not book_url or "royalroad.com" not in book_url:
        return jsonify({"error": "Invalid Royal Road URL"}), 400

    book_id, tags = await get_tags_and_id(book_url)

    if book_id and tags:
        results = await check_rising_stars(book_id, tags)
        return jsonify(results)
    else:
        return jsonify({"error": "Failed to retrieve book details"}), 500

if __name__ == '__main__':
    import os
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT, debug=True)
