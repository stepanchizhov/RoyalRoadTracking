from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# User-Agent to avoid blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# Base URL for Rising Stars
BASE_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

def get_tags_from_book(url):
    """Extracts tags from a Royal Road book page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract book title
        title_tag = soup.find("meta", {"property": "og:title"})
        book_name = title_tag["content"] if title_tag else "Unknown Title"

        # Extract tags from the book's page
        tags = []
        for tag in soup.find_all("a", class_="fiction-tag"):
            tag_url = tag["href"]
            if "tagsAdd=" in tag_url:
                tag_name = tag_url.split("tagsAdd=")[-1]
                tags.append(tag_name)

        return book_name, tags

    except requests.RequestException as e:
        return None, None

def check_rising_stars(book_name, tags):
    """Checks if the book is listed in Rising Stars under relevant tags."""
    results = {}

    for tag in tags:
        url = f"{BASE_URL}{tag}"
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Find all highlighted book titles on the Rising Stars list
            titles = [a.text.strip() for a in soup.find_all('a', class_='font-red-sunglo bold')]

            # Check if the book is present and find its ranking position
            if book_name in titles:
                position = titles.index(book_name) + 1  # Convert index to human-readable position
                results[tag] = f"✅ Found in position #{position}"
            else:
                results[tag] = "❌ Not found in this category"

        except requests.RequestException as e:
            results[tag] = f"⚠️ Failed to check: {e}"

    return results

@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    book_url = request.args.get("book_url")

    # Validate the URL
    if not book_url or "royalroad.com" not in book_url:
        return jsonify({"error": "Invalid Royal Road URL"}), 400

    # Fetch book name and tags
    book_name, tags = get_tags_from_book(book_url)

    if book_name and tags:
        results = check_rising_stars(book_name, tags)
        return jsonify(results)
    else:
        return jsonify({"error": "Failed to retrieve book details"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
