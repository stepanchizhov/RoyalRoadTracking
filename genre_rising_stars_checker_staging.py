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
    """Returns a random delay between 3-5 seconds to mimic human behavior."""
    return random.uniform(3.0, 5.0)


def fetch_with_retries(url, headers, max_retries=3, timeout=20):
    """Fetches a URL with retry logic and exponential backoff. Reduced timeout."""
    delay = 2  # Initial delay in seconds

    for attempt in range(max_retries):
        try:
            logging.info(f"🔄 Attempt {attempt + 1}/{max_retries}: Fetching {url}")
            
            # Use cloudscraper with randomized browser settings
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': random.choice(['chrome', 'firefox']), 
                    'platform': random.choice(['windows', 'darwin']), 
                    'desktop': True
                }
            )
            
            # Reduced timeout from 30 to 20 seconds
            response = scraper.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()  # Raise error for HTTP 4xx/5xx status codes
            
            # Additional check for potentially problematic responses
            if len(response.text) < 500:
                raise Exception("Response content is suspiciously short")
            
            # Add a short delay to avoid overloading the server and triggering timeouts
            time.sleep(0.5)
            
            return response  # Return response if successful
        except Exception as e:
            logging.error(f"❌ Request failed (attempt {attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                jitter = random.uniform(0, 1)
                sleep_time = delay * (2 ** attempt) + jitter
                logging.info(f"⏳ Waiting {sleep_time:.2f} seconds before retry...")
                time.sleep(sleep_time)
            else:
                logging.error(f"❌ Failed to fetch {url} after {max_retries} attempts")
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


def get_book_details_from_main_rs(headers):
    """Get details of books on the main Rising Stars list including their IDs and tags."""
    try:
        logging.info("🔍 Fetching main Rising Stars list for detailed book analysis...")
        response = fetch_with_retries(MAIN_RISING_STARS_URL, headers)
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Get all book entries on main Rising Stars
        book_entries = soup.find_all("div", class_="fiction-list-item")
        main_rs_books = []
        
        for i, entry in enumerate(book_entries):
            position = i + 1
            
            # Extract book ID from the link
            title_link = entry.find("a", class_="font-red-sunglo")
            if not title_link:
                continue
                
            book_id = title_link["href"].split("/")[2]
            
            # Extract book title
            title = title_link.text.strip()
            
            # Find tags using new method
            tags_span = entry.find("span", class_="tags")
            tags = []
            if tags_span:
                tag_links = tags_span.find_all("a", class_="fiction-tag")
                for tag_link in tag_links:
                    # Extract tag from the href attribute
                    tag = tag_link.get('href', '').split('tagsAdd=')[-1]
                    if tag and tag not in tags:
                        tags.append(tag)
            
            # If no tags found, try fallback method
            if not tags:
                tag_links = entry.find_all("a", class_="label")
                for tag_link in tag_links:
                    tag = tag_link.get('href', '').split('tagsAdd=')[-1]
                    if tag and tag not in tags:
                        tags.append(tag)
            
            main_rs_books.append({
                "position": position,
                "book_id": book_id,
                "title": title,
                "tags": tags
            })
            
            logging.info(f"📊 Main RS #{position}: {title} (ID: {book_id}) - Tags: {tags}")
        
        return main_rs_books
    
    except Exception as e:
        logging.exception(f"❌ Error fetching main Rising Stars books: {str(e)}")
        return []


def get_books_for_genre(genre, headers):
    """Get all books from a genre-specific Rising Stars list with caching."""
    # Check cache first
    cache_key = f"genre_books_{genre}"
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for genre books: {genre}")
            return cache[cache_key]
    
    try:
        url = f"{GENRE_RISING_STARS_URL}{genre}"
        logging.info(f"🔍 Fetching Rising Stars for genre: {genre} for detailed analysis...")
        
        response = fetch_with_retries(url, headers, timeout=15)  # Reduced timeout
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Get all book entries
        book_entries = soup.find_all("div", class_="fiction-list-item")
        genre_books = []
        
        for i, entry in enumerate(book_entries):
            position = i + 1
            
            # Extract book ID from the link
            title_link = entry.find("a", class_="font-red-sunglo")
            if not title_link:
                continue
                
            book_id = title_link["href"].split("/")[2]
            
            # Extract book title
            title = title_link.text.strip()
            
            genre_books.append({
                "position": position,
                "book_id": book_id,
                "title": title
            })
            
            logging.info(f"📚 {genre} RS #{position}: {title} (ID: {book_id})")
        
        # Cache the results
        with cache_lock:
            cache[cache_key] = genre_books
            
        return genre_books
    
    except Exception as e:
        logging.exception(f"❌ Error fetching genre Rising Stars books: {str(e)}")
        return []

def process_genre_estimate(genre_name, genre_position, main_rs_books, headers):
    """Helper function to process a single genre's estimate."""
    try:
        # Get all books from genre Rising Stars
        genre_books = get_books_for_genre(genre_name, headers)
        if not genre_books:
            return {"error": f"Could not fetch books for {genre_name} Rising Stars"}
        
        # Find top book (position #1) from genre
        genre_top_book = next((b for b in genre_books if b["position"] == 1), None)
        if not genre_top_book:
            logging.warning(f"⚠️ Could not find top book for {genre_name}")
            return {"error": f"Could not find top book for {genre_name}"}
        
        # Find position of genre's top book on main Rising Stars
        genre_top_book_main_position = next(
            (b["position"] for b in main_rs_books if b["book_id"] == genre_top_book["book_id"]), 
            None
        )
        
        # Find the bottom book with the same tag on main Rising Stars
        main_rs_with_tag = [b for b in main_rs_books if genre_name in b["tags"]]
        main_rs_bottom_with_tag = max(main_rs_with_tag, key=lambda x: x["position"]) if main_rs_with_tag else None
        
        genre_estimate = {}
        if genre_top_book_main_position and main_rs_bottom_with_tag:
            # Find the bottom/worst book's position in the genre list
            genre_bottom_book_id = main_rs_bottom_with_tag["book_id"]
            genre_bottom_position = None
            
            # Try to find the bottom book's position in the genre list
            for book in genre_books:
                if book["book_id"] == genre_bottom_book_id:
                    genre_bottom_position = book["position"]
                    break
            
            # If not found in the genre list, it must be lower than the lowest book in the genre list
            if genre_bottom_position is None:
                genre_bottom_position = len(genre_books) + 1
            
            # Print all the intermediate findings
            logging.info(f"📊 GENRE ANALYSIS: {genre_name}")
            logging.info(f"📊 Book position in {genre_name}: #{genre_position}")
            logging.info(f"📊 Top book from {genre_name}: {genre_top_book['title']} (ID: {genre_top_book['book_id']})")
            logging.info(f"📊 Top book position on Main RS: #{genre_top_book_main_position}")
            logging.info(f"📊 Bottom book from Main RS with {genre_name} tag: {main_rs_bottom_with_tag['title']} (pos #{main_rs_bottom_with_tag['position']})")
            logging.info(f"📊 Bottom book position in {genre_name} list: {genre_bottom_position}")
            
            # Calculate scaling factor based on the positions we have
            main_rs_span = main_rs_bottom_with_tag["position"] - genre_top_book_main_position
            if main_rs_span <= 0:
                main_rs_span = 1  # Minimum span to avoid division by zero
                
            genre_rs_span = genre_bottom_position - 1  # 1 is the top position
            if genre_rs_span <= 0:
                genre_rs_span = 1  # Minimum span to avoid division by zero
            
            # Calculate scaling factor (how many main RS positions per genre position)
            scaling_factor = main_rs_span / genre_rs_span
            
            # Calculate estimated positions to Main RS
            positions_to_scale = genre_position - 1  # Distance from top
            estimated_distance = max(1, int(positions_to_scale * scaling_factor))
            estimated_position = genre_top_book_main_position + estimated_distance
            
            genre_estimate = {
                "genre": genre_name,
                "book_genre_position": genre_position,
                "top_book_main_position": genre_top_book_main_position,
                "bottom_tag_book_main_position": main_rs_bottom_with_tag["position"],
                "bottom_tag_book_genre_position": genre_bottom_position,
                "scaling_factor": scaling_factor,
                "estimated_distance": estimated_distance,
                "estimated_position": estimated_position,
                "main_rs_size": len(main_rs_books),
                "positions_away_from_bottom": max(0, len(main_rs_books) - estimated_position)
            }
            
            # Log the estimate
            logging.info(f"📊 ESTIMATE: Book would be around position #{estimated_position} on Main RS")
            logging.info(f"📊 This is {estimated_distance} positions away from the top book of {genre_name}")
            if estimated_position <= len(main_rs_books):
                logging.info(f"📊 The book is estimated to be IN the Main Rising Stars list!")
            else:
                positions_away = estimated_position - len(main_rs_books)
                logging.info(f"📊 The book is estimated to be {positions_away} positions away from joining Main Rising Stars")
        else:
            genre_estimate = {
                "message": f"Could not find enough reference books to make an estimate from {genre_name}",
                "genre": genre_name,
                "book_genre_position": genre_position,
                "top_book_main_position": genre_top_book_main_position,
                "main_rs_size": len(main_rs_books)
            }
        
        return genre_estimate
        
    except Exception as e:
        logging.exception(f"❌ Error processing genre estimate for {genre_name}: {str(e)}")
        return {
            "error": f"Error processing genre: {str(e)}",
            "genre": genre_name,
            "book_genre_position": genre_position
        }

def create_combined_estimate(best_estimate, worst_estimate, main_rs_size):
    """Creates a combined estimate from best and worst genre estimates."""
    combined_estimate = {}
    
    if "estimated_position" in best_estimate and worst_estimate and "estimated_position" in worst_estimate:
        # Use the average if we have both estimates
        combined_position = (best_estimate["estimated_position"] + worst_estimate["estimated_position"]) / 2
        combined_estimate = {
            "estimated_position": int(combined_position),
            "best_genre_estimate": best_estimate["estimated_position"],
            "worst_genre_estimate": worst_estimate["estimated_position"],
            "main_rs_size": main_rs_size
        }
        
        if combined_position <= main_rs_size:
            combined_estimate["status"] = "IN_RANGE"
            combined_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at around position #{int(combined_position)}"
        else:
            positions_away = int(combined_position - main_rs_size)
            combined_estimate["status"] = "OUTSIDE_RANGE"
            combined_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
            combined_estimate["positions_away"] = positions_away
    elif "estimated_position" in best_estimate:
        # Only have best genre estimate
        if best_estimate["estimated_position"] <= main_rs_size:
            combined_estimate["status"] = "IN_RANGE"
            combined_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at around position #{best_estimate['estimated_position']}"
        else:
            positions_away = best_estimate["estimated_position"] - main_rs_size
            combined_estimate["status"] = "OUTSIDE_RANGE"
            combined_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
            combined_estimate["positions_away"] = positions_away
        
        combined_estimate["estimated_position"] = best_estimate["estimated_position"]
        combined_estimate["best_genre_estimate"] = best_estimate["estimated_position"]
        combined_estimate["main_rs_size"] = main_rs_size
    else:
        # No valid estimates available
        combined_estimate["status"] = "UNKNOWN"
        combined_estimate["message"] = "Could not calculate a position estimate with the available data"
        combined_estimate["main_rs_size"] = main_rs_size
    
    return combined_estimate

def estimate_distance_to_main_rs(book_id, genre_results, tags, headers):
    """
    Estimate how far the book is from the main Rising Stars list.
    Modified to handle timeouts better and process incrementally.
    """
    # Check cache first
    cache_key = f"distance_estimate_{book_id}"
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for distance estimate: {book_id}")
            return cache[cache_key]
    
    estimates = {}
    
    try:
        # Get main Rising Stars books with their tags - this is a critical operation
        cache_key_main = "main_rs_books"
        with cache_lock:
            if cache_key_main in cache:
                main_rs_books = cache[cache_key_main]
                logging.info(f"📋 Cache hit for main Rising Stars books")
            else:
                main_rs_books = get_book_details_from_main_rs(headers)
                if main_rs_books:
                    with cache_lock:
                        cache[cache_key_main] = main_rs_books
        
        if not main_rs_books:
            return {"error": "Could not fetch main Rising Stars data for estimation"}
        
        # Create a dictionary of genre -> position for this book
        book_positions = {}
        for genre, status in genre_results.items():
            if status.startswith("✅ Found in position #"):
                position = int(re.search(r"#(\d+)", status).group(1))
                book_positions[genre] = position
        
        if not book_positions:
            return {"message": "Book not found in any genre Rising Stars lists, cannot estimate distance"}
        
        # Find best and worst genres (where book has highest and lowest position)
        best_genre = min(book_positions.items(), key=lambda x: x[1])
        worst_genre = max(book_positions.items(), key=lambda x: x[1])
        
        # Process best genre first
        best_genre_name, best_genre_position = best_genre
        logging.info(f"🌟 Book has best position in {best_genre_name} at #{best_genre_position}")
        
        # We'll process one genre at a time to avoid timeouts
        # Start with best genre
        best_estimate = process_genre_estimate(
            best_genre_name, 
            best_genre_position, 
            main_rs_books, 
            headers
        )
        estimates["best_genre_estimate"] = best_estimate
        
        # Now do the same for worst genre if different from best genre
        worst_genre_name, worst_genre_position = worst_genre
        if worst_genre_name != best_genre_name:  # Only do this if different from best genre
            logging.info(f"🔍 Book has worst position in {worst_genre_name} at #{worst_genre_position}")
            
            worst_estimate = process_genre_estimate(
                worst_genre_name, 
                worst_genre_position, 
                main_rs_books, 
                headers
            )
            estimates["worst_genre_estimate"] = worst_estimate
        
        # Create a combined estimate
        combined_estimate = create_combined_estimate(best_estimate, 
                                                     estimates.get("worst_genre_estimate"),
                                                     len(main_rs_books))
        
        estimates["combined_estimate"] = combined_estimate
        
        # Cache the result
        with cache_lock:
            cache[f"distance_estimate_{book_id}"] = estimates
            
        return estimates
    
    except Exception as e:
        logging.exception(f"❌ Error estimating distance to main Rising Stars: {str(e)}")
        return {"error": f"Error estimating distance: {str(e)}"}


def check_rising_stars(book_id, tags, start_index=0):
    """Checks if the book appears in the main and genre-specific Rising Stars lists."""
    results = {}
    
    # Check the Main Rising Stars list first
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        logging.info("🔍 Checking Main Rising Stars list...")

        response = fetch_with_retries(MAIN_RISING_STARS_URL, headers)

        soup = BeautifulSoup(response.text, 'html.parser')
        book_links = soup.find_all("a", class_="font-red-sunglo")
        book_ids = [link.get('href', '').split('/')[2] for link in book_links]

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

    # Check each genre's Rising Stars page starting from the given index
    for tag in tags[start_index:]:
        try:
            url = f"{GENRE_RISING_STARS_URL}{tag}"
            logging.info(f"🔍 Checking Rising Stars for genre: {tag}")
            
            response = fetch_with_retries(url, headers)

            soup = BeautifulSoup(response.text, 'html.parser')
            book_links = soup.find_all("a", class_="font-red-sunglo")
            book_ids = [link.get('href', '').split('/')[2] for link in book_links]

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
            
            # Return partial results and the index of the failed tag
            return results, tags.index(tag)

    return results, len(tags)

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
    start_time = time.time()
    request_id = f"req_{random.randint(10000, 99999)}"
    
    book_url = request.args.get('book_url', '').strip()
    estimate_distance_param = request.args.get('estimate_distance', 'false').lower() == 'true'
    
    # Logging for debugging
    logging.critical(f"🔍 [{request_id}] Received book_url: {book_url}")
    logging.critical(f"🔍 [{request_id}] Estimate distance: {estimate_distance_param}")

    # Validate book URL
    if not book_url or "royalroad.com" not in book_url:
        logging.error(f"❌ [{request_id}] Invalid Royal Road URL")
        return jsonify({
            "error": "Invalid Royal Road URL", 
            "results": {}, 
            "title": "Unknown Title",
            "request_id": request_id,
            "processing_time": f"{time.time() - start_time:.2f} seconds"
        }), 400

    # Get book details
    try:
        title, book_id, tags = get_title_and_tags(book_url)

        if not book_id or not tags:
            logging.error(f"❌ [{request_id}] Failed to retrieve book details")
            return jsonify({
                "error": "Failed to retrieve book details", 
                "title": title, 
                "results": {},
                "request_id": request_id,
                "processing_time": f"{time.time() - start_time:.2f} seconds"
            }), 500
    
    except Exception as e:
        logging.exception(f"❌ [{request_id}] Unexpected error processing book URL: {str(e)}")
        return jsonify({
            "error": f"Unexpected error: {str(e)}",
            "request_id": request_id,
            "processing_time": f"{time.time() - start_time:.2f} seconds"
        }), 500
    
    # Prepare headers for requests
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.royalroad.com/",
        "DNT": "1"
    }
    
    # Approach to handle partial results with correct continuation
    final_results = {}
    
    # Check if we have cached results for main rising stars
    cache_key_main_result = f"main_rs_result_{book_id}"
    with cache_lock:
        if cache_key_main_result in cache:
            final_results["Main Rising Stars"] = cache[cache_key_main_result]
            logging.info(f"📋 [{request_id}] Cache hit for main rising stars result: {book_id}")
        else:
            # Ensure we start with the results from checking the Main Rising Stars
            try:
                logging.info(f"🔍 [{request_id}] Checking Main Rising Stars list...")
                response = fetch_with_retries(MAIN_RISING_STARS_URL, headers, timeout=15)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                book_links = soup.find_all("a", class_="font-red-sunglo")
                book_ids = []
                for link in book_links:
                    try:
                        link_parts = link.get('href', '').split('/')
                        if len(link_parts) >= 3:
                            book_ids.append(link_parts[2])
                    except Exception as e:
                        logging.warning(f"⚠️ [{request_id}] Error extracting book ID from link: {e}")

                if book_id in book_ids:
                    position = book_ids.index(book_id) + 1
                    main_result = f"✅ Found in position #{position}"
                    logging.info(f"✅ [{request_id}] Book {book_id} found in Main Rising Stars at position {position}")
                else:
                    main_result = "❌ Not found in Main Rising Stars list"
                    logging.info(f"❌ [{request_id}] Book {book_id} not found in Main Rising Stars")
                
                final_results["Main Rising Stars"] = main_result
                
                # Cache the result
                with cache_lock:
                    cache[cache_key_main_result] = main_result
            
            except Exception as e:
                logging.exception(f"⚠️ [{request_id}] Failed to check Main Rising Stars: {str(e)}")
                final_results["Main Rising Stars"] = f"⚠️ Failed to check: {str(e)}"
    
    # Check for cached genre results
    genres_processed = []
    for tag in tags:
        cache_key_genre = f"genre_result_{book_id}_{tag}"
        with cache_lock:
            if cache_key_genre in cache:
                final_results[tag] = cache[cache_key_genre]
                genres_processed.append(tag)
                logging.info(f"📋 [{request_id}] Cache hit for genre result: {tag}")
    
    # Process remaining genres
    remaining_tags = [tag for tag in tags if tag not in genres_processed]
    for tag in remaining_tags:
        try:
            url = f"{GENRE_RISING_STARS_URL}{tag}"
            logging.info(f"🔍 [{request_id}] Checking Rising Stars for genre: {tag}")
            
            response = fetch_with_retries(url, headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            book_links = soup.find_all("a", class_="font-red-sunglo")
            book_ids = [link.get('href', '').split('/')[2] for link in book_links]

            if book_id in book_ids:
                position = book_ids.index(book_id) + 1
                result = f"✅ Found in position #{position}"
                logging.info(f"✅ [{request_id}] Book {book_id} found in {tag} at position {position}")
            else:
                result = f"❌ Not found in '{tag}' Rising Stars list"
                logging.info(f"❌ [{request_id}] Book {book_id} not found in {tag}")
            
            final_results[tag] = result
            
            # Cache the result
            with cache_lock:
                cache[f"genre_result_{book_id}_{tag}"] = result

        except Exception as e:
            logging.exception(f"⚠️ [{request_id}] Failed to check {tag} Rising Stars: {str(e)}")
            final_results[tag] = f"⚠️ Failed to check: {str(e)}"
    
    # Distance estimation logic with improved error handling
    distance_estimate = {}
    if estimate_distance_param:
        try:
            cache_key_distance = f"distance_estimate_{book_id}"
            with cache_lock:
                if cache_key_distance in cache:
                    distance_estimate = cache[cache_key_distance]
                    logging.info(f"📋 [{request_id}] Cache hit for distance estimate: {book_id}")
                else:
                    distance_estimate = estimate_distance_to_main_rs(book_id, final_results, tags, headers)
                    # Cache is handled inside the estimate_distance_to_main_rs function
        except Exception as e:
            logging.critical(f"❌ [{request_id}] CRITICAL ERROR during distance estimation: {str(e)}")
            distance_estimate = {"error": f"Error during estimation: {str(e)}"}
    
    # Build response
    response_data = {
        "title": title, 
        "results": final_results,
        "book_id": book_id,
        "tags": tags,
        "request_id": request_id,
        "processing_time": f"{time.time() - start_time:.2f} seconds"
    }
    
    # Add distance estimate if it was requested and generated
    if estimate_distance_param and distance_estimate:
        response_data["distance_estimate"] = distance_estimate
        logging.critical(f"✅ [{request_id}] Distance estimate ADDED to response")
    
    return jsonify(response_data)

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
