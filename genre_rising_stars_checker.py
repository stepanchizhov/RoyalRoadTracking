from flask import Flask, request, jsonify
from flask_cors import CORS  # ✅ Enable cross-origin requests for WordPress
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import re
import random

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


async def fetch_page(session, url):
    """Asynchronously fetches a webpage and returns the parsed HTML."""
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    try:
        async with session.get(url, headers=headers, timeout=15) as response:
            response.raise_for_status()
            return await response.text()
    except Exception as e:
        print(f"⚠️ Failed to fetch {url}: {e}")
        return None


def extract_book_id(book_url):
    """Extracts the book ID from a Royal Road book URL."""
    match = re.search(r'/fiction/(\d+)/', book_url)
    return match.group(1) if match else None


async def get_tags_and_id(book_url):
    """Asynchronously extracts book ID and tags from a Royal Road book page."""
    async with aiohttp.ClientSession() as session:
        html = await fetch_page(session, book_url)
        if not html:
            return None, None

        soup = BeautifulSoup(html, "html.parser")
        book_id = extract_book_id(book_url)

        # Extract tags
        tags = []
        for tag in soup.find_all("a", class_="fiction-tag"):
            tag_url = tag["href"]
            if "tagsAdd=" in tag_url:
                tag_name = tag_url.split("tagsAdd=")[-1]
                tags.append(tag_name)

        return book_id, tags


async def check_rising_stars(book_id, tags):
    """Asynchronously checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}

    async with aiohttp.ClientSession() as session:
        # Check the Main Rising Stars list first
        html = await fetch_page(session, MAIN_RISING_STARS_URL)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                results["Main Rising Stars"] = f"✅ Found in position #{position}"
            else:
                results["Main Rising Stars"] = "❌ Not found in Main Rising Stars list"
        else:
            results["Main Rising Stars"] = "⚠️ Failed to fetch Main Rising Stars list"

        # Check each genre's Rising Stars page
        for tag in tags:
            await asyncio.sleep(5)  # ✅ Delay to avoid rate-limiting

            url = f"{GENRE_RISING_STARS_URL}{tag}"
            html = await fetch_page(session, url)
            if html:
                soup = BeautifulSoup(html, "html.parser")
                book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

                if book_id in book_ids:
                    position = book_ids.index(book_id) + 1
                    results[tag] = f"✅ Found in position #{position}"
                else:
                    results[tag] = f"❌ Not found in '{tag}' Rising Stars list"
            else:
                results[tag] = f"⚠️ Failed to fetch '{tag}' Rising Stars list"

    return results


@app.route('/check_rising_stars', methods=['GET'])
async def api_rising_stars():
    book_url = request.args.get("book_url")

    # Validate the URL
    if not book_url or "royalroad.com" not in book_url:
        return jsonify({"error": "Invalid Royal Road URL provided."}), 400

    try:
        # Fetch book ID and tags
        book_id, tags = await get_tags_and_id(book_url)

        if not book_id:
            return jsonify({"error": "Failed to extract book ID from the provided URL."}), 400

        if not tags:
            return jsonify({"error": "Failed to retrieve tags for this book."}), 400

        results = await check_rising_stars(book_id, tags)
        return jsonify(results)

    except Exception as e:
        return jsonify({"error": f"Unexpected server error: {str(e)}"}), 500


if __name__ == '__main__':
    import os
    import uvicorn

    PORT = int(os.environ.get("PORT", 10000))  # ✅ Uses PORT=10000 (fixes Render deployment issues)
    
    # ✅ Use ASGI server to support async operations
    uvicorn.run(app, host="0.0.0.0", port=PORT)
