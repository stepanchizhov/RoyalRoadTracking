from flask import Flask, request, jsonify
from flask_cors import CORS
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import random
import time
import cachetools
import threading
from datetime import datetime, timedelta

# Enhanced logging with timestamps and thread info
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s"
)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Create a cache with TTL (time-to-live) of 30 minutes
# Max size of 100 entries to prevent memory issues
cache = cachetools.TTLCache(maxsize=100, ttl=30*60)  # 30 minutes TTL
cache_lock = threading.RLock()  # Thread-safe lock for cache operations

# User-Agent rotation with more modern browsers
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:112.0) Gecko/20100101 Firefox/112.0"
]

# Base URLs
MAIN_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars"
GENRE_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

# Initialize Cloudscraper with more browser options for better anti-bot avoidance
def get_scraper():
    """Creates a new cloudscraper instance with random browser settings."""
    browser_options = [
        {'browser': 'firefox', 'platform': 'windows', 'desktop': True},
        {'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        {'browser': 'chrome', 'platform': 'darwin', 'desktop': True},  # macOS
        {'browser': 'firefox', 'platform': 'darwin', 'desktop': True},  # macOS
    ]
    
    selected_browser = random.choice(browser_options)
    return cloudscraper.create_scraper(browser=selected_browser)


def extract_book_id(book_url):
    """Extracts the book ID from a Royal Road book URL."""
    # First try the standard pattern
    match = re.search(r'/fiction/(\d+)', book_url)
    if match:
        return match.group(1)
    
    # Try alternative patterns (handle URLs with slugs or without trailing slashes)
    match = re.search(r'fiction/(\d+)(?:/[^/]+)?/?$', book_url)
    if match:
        return match.group(1)
    
    # Try to extract just numbers if all else fails
    numbers = re.findall(r'(\d+)', book_url)
    if numbers and len(numbers[0]) > 3:  # Assuming book IDs are longer than 3 digits
        return numbers[0]
    
    return None


def get_random_delay():
    """Returns a random delay between 1-3 seconds to mimic human behavior."""
    return random.uniform(1.0, 3.0)


def fetch_with_retries(url, headers, max_retries=4):
    """Fetches a URL with retry logic, exponential backoff, and human-like behavior."""
    delay = 2  # Initial delay in seconds
    scraper = get_scraper()  # Get a fresh scraper for each request
    
    for attempt in range(max_retries):
        try:
            # Log request attempt with clear emoji
            logging.info(f"🔄 Attempt {attempt + 1}/{max_retries}: Fetching {url}")
            
            # Add randomized delay between requests to avoid detection
            if attempt > 0:
                time.sleep(delay + get_random_delay())
            
            # Make the request with timeout
            response = scraper.get(url, headers=headers, timeout=30)
            
            # Check if response is valid
            response.raise_for_status()
            
            # Verify we got actual content and not a CAPTCHA page
            if "captcha" in response.text.lower() or len(response.text) < 500:
                raise Exception("Possible CAPTCHA page or empty response detected")
            
            # Short delay to mimic reading the page
            time.sleep(get_random_delay() / 2)
            
            # Log success
            logging.info(f"✅ Successfully fetched data from {url}")
            
            return response
            
        except Exception as e:
            error_type = type(e).__name__
            logging.error(f"❌ Request failed (attempt {attempt + 1}/{max_retries}): {error_type}: {e}")
            
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                jitter = random.uniform(0, 1)
                delay = min(30, delay * 2 + jitter)  # Cap at 30 seconds
                logging.info(f"⏱️ Retrying in {delay:.2f} seconds...")
            else:
                raise Exception(f"Failed to fetch data after {max_retries} attempts: {e}")


def get_title_and_tags(book_url, book_id=None):
    """Extracts book title, ID, and tags from a Royal Road book page."""
    if not book_id:
        book_id = extract_book_id(book_url)
        if not book_id:
            logging.error("❌ Failed to extract book ID from URL")
            return "Unknown Title", None, []
    
    # Check cache first
    cache_key = f"book_info_{book_id}"
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for book ID {book_id}")
            return cache[cache_key]
    
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.royalroad.com/",
            "DNT": "1"
        }
        
        logging.info(f"📚 Fetching book page for ID: {book_id}")
        
        # Fetch the book's direct page using the book ID
        book_page_url = f"https://www.royalroad.com/fiction/{book_id}/"
        logging.info(f"📄 Fetching book's main page: {book_page_url}")
        
        book_response = fetch_with_retries(book_page_url, headers)
        
        book_soup = BeautifulSoup(book_response.text, "html.parser")
        
        # Extract title
        title_tag = book_soup.find("h1", class_="font-white")
        if title_tag:
            title = title_tag.text.strip()
        else:
            title_tag = book_soup.find("title")
            if title_tag:
                title = title_tag.text.strip().replace(" | Royal Road", "")
                title = re.sub(r'&#\d+;', '', title)  # Remove HTML entities
            else:
                title = "Unknown Title"
        
        # Extract tags from the book's page - using multiple methods for reliability
        tags = []
        
        # Method 1: Look for span with fiction-tag class
        tag_elements = book_soup.find_all("span", class_="fiction-tag")
        if tag_elements:
            for tag in tag_elements:
                tag_text = tag.get_text().strip()
                if tag_text:
                    tags.append(tag_text)
        
        # Method 2: Look for links with fiction-tag class
        if not tags:
            tag_links = book_soup.find_all("a", class_="fiction-tag")
            for tag in tag_links:
                if "tagsAdd=" in tag.get("href", ""):
                    tag_value = tag.get("href", "").split("tagsAdd=")[-1]
                    tags.append(tag_value)
        
        # Method 3: Look for fic-genres section
        if not tags:
            genres_section = book_soup.find("div", class_="fic-genres")
            if genres_section:
                genre_links = genres_section.find_all("a")
                for link in genre_links:
                    tag_text = link.get_text().strip()
                    if tag_text:
                        tags.append(tag_text)
        
        logging.info(f"✅ Extracted Book Title: {title}, ID: {book_id}, Tags: {tags}")
        
        # Store result in cache
        result = (title, book_id, tags)
        with cache_lock:
            cache[cache_key] = result
            
        return result
        
    except Exception as e:
        logging.exception(f"❌ Error fetching book details: {str(e)}")
        return "Unknown Title", book_id, []


def check_rising_stars(book_id, tags):
    """Checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}
    
    # Check cache first
    cache_key = f"rising_stars_{book_id}"
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for rising stars data for book ID {book_id}")
            return cache[cache_key]

    # Check the Main Rising Stars list first
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.royalroad.com/",
            "DNT": "1"
        }
        
        logging.info("🔍 Checking Main Rising Stars list...")

        response = fetch_with_retries(MAIN_RISING_STARS_URL, headers)

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
        logging.exception(f"⚠️ Failed to check Main Rising Stars: {str(e)}")
        results["Main Rising Stars"] = f"⚠️ Failed to check: {str(e)}"

    # Check each genre's Rising Stars page
    for tag in tags:
        url = f"{GENRE_RISING_STARS_URL}{tag}"
        try:
            logging.info(f"🔍 Checking Rising Stars for genre: {tag}")
            
            # Add a small delay between requests to avoid rate limiting
            time.sleep(get_random_delay())
            
            response = fetch_with_retries(url, headers)

            soup = BeautifulSoup(response.text, "html.parser")
            book_ids = [a["href"].split("/")[2] for a in soup.find_all("a", class_="font-red-sunglo bold")]

            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                results[tag] = f"✅ Found in position #{position}"
                logging.info(f"✅ Book {book_id} found in {tag} at position {position}")
            else:
                results[tag] = f"❌ Not found in '{tag}' Rising Stars list"
                logging.info(f"❌ Book {book_id} not found in {tag}")

        except Exception as e:
            logging.exception(f"⚠️ Failed to check {tag} Rising Stars: {str(e)}")
            results[tag] = f"⚠️ Failed to check: {str(e)}"

    # Store results in cache
    with cache_lock:
        cache[cache_key] = results
        
    return results


@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({
        "status": "healthy",
        "time": datetime.now().isoformat(),
        "cache_size": len(cache),
        "cache_maxsize": cache.maxsize
    })


@app.route('/cache/clear', methods=['POST'])
def clear_cache():
    """Endpoint to clear the cache if needed."""
    api_key = request.headers.get('X-API-Key')
    if not api_key or api_key != "YOUR_SECRET_API_KEY":  # Replace with your actual secret key in production
        return jsonify({"error": "Unauthorized"}), 403
    
    with cache_lock:
        cache.clear()
    
    return jsonify({"status": "success", "message": "Cache cleared"})


@app.route('/check_rising_stars', methods=['GET'])
def api_rising_stars():
    """Main API endpoint to check rising stars for a book."""
    start_time = time.time()
    book_url = request.args.get("book_url")
    
    request_id = f"req_{int(time.time())}_{random.randint(1000, 9999)}"
    logging.info(f"[{request_id}] 📝 Received request for book URL: {book_url}")

    # Validate input
    if not book_url:
        logging.error(f"[{request_id}] ❌ Missing book URL")
        return jsonify({
            "error": "Missing book URL parameter", 
            "results": {}, 
            "title": "Unknown Title"
        }), 400
        
    if "royalroad.com" not in book_url:
        logging.error(f"[{request_id}] ❌ Invalid Royal Road URL: {book_url}")
        return jsonify({
            "error": "Invalid Royal Road URL. URL must be from royalroad.com", 
            "results": {}, 
            "title": "Unknown Title"
        }), 400

    try:
        # Extract book ID from URL first
        book_id = extract_book_id(book_url)
        if not book_id:
            logging.error(f"[{request_id}] ❌ Could not extract book ID from URL: {book_url}")
            return jsonify({
                "error": "Could not extract book ID from URL", 
                "results": {}, 
                "title": "Unknown Title"
            }), 400
            
        # Get title and tags
        title, book_id, tags = get_title_and_tags(book_url, book_id)
        
        if not tags:
            logging.warning(f"[{request_id}] ⚠️ No tags found for book ID {book_id}")
            
        # Check rising stars
        results = check_rising_stars(book_id, tags)
        
        # Calculate processing time
        processing_time = time.time() - start_time
        logging.info(f"[{request_id}] ✅ Request completed in {processing_time:.2f} seconds")
        
        return jsonify({
            "title": title, 
            "results": results,
            "book_id": book_id,
            "tags": tags,
            "processing_time": f"{processing_time:.2f} seconds"
        })
        
    except Exception as e:
        error_type = type(e).__name__
        logging.exception(f"[{request_id}] ❌ Error processing request: {error_type}: {str(e)}")
        
        return jsonify({
            "error": f"Failed to process request: {str(e)}", 
            "title": getattr(locals(), 'title', "Unknown Title"), 
            "results": {}
        }), 500


# Function to periodically clean up cache (optional, for long-running servers)
def cache_cleanup():
    """Clean up expired cache entries to free memory."""
    while True:
        time.sleep(3600)  # Run every hour
        try:
            with cache_lock:
                # Note: TTLCache automatically removes expired entries on access,
                # but this forces a cleanup even without access
                old_size = len(cache)
                for key in list(cache.keys()):
                    # Just accessing each key will trigger TTLCache's cleanup mechanism
                    _ = cache.get(key)
                current_size = len(cache)
                
                if old_size > current_size:
                    logging.info(f"🧹 Cache cleanup: removed {old_size - current_size} expired entries")
        except Exception as e:
            logging.error(f"❌ Error during cache cleanup: {e}")


if __name__ == '__main__':
    import os
    
    # Start cache cleanup in a background thread
    cleanup_thread = threading.Thread(target=cache_cleanup, daemon=True)
    cleanup_thread.start()
    
    # Get port from environment variable, default to 10000
    PORT = int(os.environ.get("PORT", 10000))
    
    # Start the Flask app
    logging.info(f"🚀 Starting server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
