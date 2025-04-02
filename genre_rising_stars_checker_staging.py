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


def fetch_with_retries(url, headers, max_retries=4, base_delay=2, max_delay=30):
    """Fetches a URL with retry logic, exponential backoff, and human-like behavior."""
    delay = base_delay  # Initial delay in seconds
    scraper = get_scraper()  # Get a fresh scraper for each request
    
    for attempt in range(max_retries):
        try:
            # Log request attempt with clear emoji
            logging.info(f"🔄 Attempt {attempt + 1}/{max_retries}: Fetching {url}")
            
            # Add randomized delay between requests to avoid detection
            if attempt > 0:
                time.sleep(delay + get_random_delay())
            
            # Make the request with timeout
            response = scraper.get(url, headers=headers, timeout=15)  # Increased timeout to 15 seconds
            
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
                delay = min(max_delay, delay * 2 + jitter)  # Cap at max_delay
                logging.info(f"⏱️ Retrying in {delay:.2f} seconds...")
            else:
                logging.error(f"❌ Completely failed to fetch {url} after {max_retries} attempts")
                raise


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
    cache_key = "main_rs_books"
    
    # Check cache first
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for main Rising Stars books")
            return cache[cache_key]
    
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
            
            # Extract tags - try a few methods
            tags = []
            tag_elements = entry.find_all("span", class_="label")
            for tag_el in tag_elements:
                tag_text = tag_el.text.strip()
                if tag_text and not tag_text.startswith("New!"):  # Skip "New!" labels
                    tags.append(tag_text)
            
            # If no tags found, get them from the book's page
            if not tags:
                _, _, tags = get_title_and_tags("", book_id)
            
            main_rs_books.append({
                "position": position,
                "book_id": book_id,
                "title": title,
                "tags": tags
            })
            
            logging.info(f"📊 Main RS #{position}: {title} (ID: {book_id}) - Tags: {tags}")
        
        # Store in cache
        with cache_lock:
            cache[cache_key] = main_rs_books
            
        return main_rs_books
    
    except Exception as e:
        logging.exception(f"❌ Error fetching main Rising Stars books: {str(e)}")
        return []


def get_books_for_genre(genre, headers):
    """Get all books from a genre-specific Rising Stars list."""
    cache_key = f"genre_books_{genre}"
    
    # Check cache first
    with cache_lock:
        if cache_key in cache:
            logging.info(f"📋 Cache hit for {genre} Rising Stars books")
            return cache[cache_key]
    
    try:
        url = f"{GENRE_RISING_STARS_URL}{genre}"
        logging.info(f"🔍 Fetching Rising Stars for genre: {genre} for detailed analysis...")
        
        response = fetch_with_retries(url, headers)
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
        
        # Store in cache
        with cache_lock:
            cache[cache_key] = genre_books
            
        return genre_books
    
    except Exception as e:
        logging.exception(f"❌ Error fetching genre Rising Stars books: {str(e)}")
        return []


def estimate_distance_to_main_rs(book_id, genre_results, tags, headers):
    """
    Estimate how far the book is from the main Rising Stars list.
    
    The algorithm works as follows:
    1. Find the genre where the book has the best position
    2. Get books from that genre's Rising Stars list
    3. Get books from the main Rising Stars list
    4. Find the top book from the genre list and its position on the main list
    5. Find the bottom book with the same tag on the main list
    6. Calculate the relative position and estimate distance
    """
    estimates = {}
    
    try:
        # Get main Rising Stars books with their tags
        main_rs_books = get_book_details_from_main_rs(headers)
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
        
        # Get all books from best genre Rising Stars
        best_genre_books = get_books_for_genre(best_genre_name, headers)
        if not best_genre_books:
            return {"error": f"Could not fetch books for {best_genre_name} Rising Stars"}
        
        # Find top book (position #1) from best genre
        best_genre_top_book = next((b for b in best_genre_books if b["position"] == 1), None)
        if not best_genre_top_book:
            logging.warning(f"⚠️ Could not find top book for {best_genre_name}")
            return {"error": f"Could not find top book for {best_genre_name}"}
        
        # Find position of best genre's top book on main Rising Stars
        best_genre_top_book_main_position = next(
            (b["position"] for b in main_rs_books if b["book_id"] == best_genre_top_book["book_id"]), 
            None
        )
        
        # Find the bottom book with the same tag on main Rising Stars
        main_rs_with_tag = [b for b in main_rs_books if best_genre_name in b["tags"]]
        main_rs_bottom_with_tag = max(main_rs_with_tag, key=lambda x: x["position"]) if main_rs_with_tag else None
        
        best_estimate = {}
        if best_genre_top_book_main_position and main_rs_bottom_with_tag:
            # Calculate scaling factor
            best_genre_bottom_position = next(
                (b["position"] for b in best_genre_books if b["book_id"] == main_rs_bottom_with_tag["book_id"]),
                len(best_genre_books)  # Assume it would be at the bottom if not found
            )
            
            # Print all the intermediate findings
            logging.info(f"📊 BEST GENRE ANALYSIS:")
            logging.info(f"📊 Genre: {best_genre_name}, Book position: #{best_genre_position}")
            logging.info(f"📊 Top book from {best_genre_name}: {best_genre_top_book['title']} (ID: {best_genre_top_book['book_id']})")
            logging.info(f"📊 Top book position on Main RS: #{best_genre_top_book_main_position}")
            logging.info(f"📊 Bottom book from Main RS with {best_genre_name} tag: {main_rs_bottom_with_tag['title']} (pos #{main_rs_bottom_with_tag['position']})")
            
            if best_genre_bottom_position > best_genre_position:
                # Calculate how many positions between top and bottom books
                main_rs_span = main_rs_bottom_with_tag["position"] - best_genre_top_book_main_position
                genre_rs_span = best_genre_bottom_position - 1  # 1 is the top position
                
                # Calculate scaling factor (how many main RS positions per genre position)
                scaling_factor = main_rs_span / genre_rs_span if genre_rs_span > 0 else 1
                
                # Calculate estimated positions to Main RS
                positions_to_scale = best_genre_position - 1  # Distance from top
                estimated_distance = int(positions_to_scale * scaling_factor)
                estimated_position = best_genre_top_book_main_position + estimated_distance
                
                best_estimate = {
                    "genre": best_genre_name,
                    "book_genre_position": best_genre_position,
                    "top_book_main_position": best_genre_top_book_main_position,
                    "bottom_tag_book_main_position": main_rs_bottom_with_tag["position"],
                    "bottom_tag_book_genre_position": best_genre_bottom_position,
                    "scaling_factor": scaling_factor,
                    "estimated_distance": estimated_distance,
                    "estimated_position": estimated_position,
                    "main_rs_size": len(main_rs_books),
                    "positions_away_from_bottom": max(0, len(main_rs_books) - estimated_position)
                }
                
                # Log the estimate
                logging.info(f"📈 BEST ESTIMATE: Book would be around position #{estimated_position} on Main RS")
                logging.info(f"📈 This is {estimated_distance} positions away from the top book of {best_genre_name}")
                if estimated_position <= len(main_rs_books):
                    logging.info(f"📈 The book is estimated to be IN the Main Rising Stars list!")
                else:
                    positions_away = estimated_position - len(main_rs_books)
                    logging.info(f"📈 The book is estimated to be {positions_away} positions away from joining Main Rising Stars")
            else:
                best_estimate = {
                    "message": f"Book is already higher than the lowest {best_genre_name} book on Main RS",
                    "genre": best_genre_name,
                    "book_genre_position": best_genre_position,
                    "top_book_main_position": best_genre_top_book_main_position,
                    "bottom_tag_book_main_position": main_rs_bottom_with_tag["position"],
                    "main_rs_size": len(main_rs_books)
                }
        else:
            best_estimate = {
                "message": "Could not find enough reference books to make an estimate from best genre",
                "genre": best_genre_name,
                "book_genre_position": best_genre_position,
                "top_book_main_position": best_genre_top_book_main_position,
                "main_rs_size": len(main_rs_books)
            }
        
        estimates["best_genre_estimate"] = best_estimate
        
        # Now do the same for worst genre
        worst_genre_name, worst_genre_position = worst_genre
        if worst_genre_name != best_genre_name:  # Only do this if different from best genre
            logging.info(f"🔍 Book has worst position in {worst_genre_name} at #{worst_genre_position}")
            
            # Get all books from worst genre Rising Stars
            worst_genre_books = get_books_for_genre(worst_genre_name, headers)
            if not worst_genre_books:
                estimates["worst_genre_estimate"] = {"error": f"Could not fetch books for {worst_genre_name} Rising Stars"}
                return estimates
            
            # Find top book (position #1) from worst genre
            worst_genre_top_book = next((b for b in worst_genre_books if b["position"] == 1), None)
            if not worst_genre_top_book:
                estimates["worst_genre_estimate"] = {"error": f"Could not find top book for {worst_genre_name}"}
                return estimates
            
            # Find position of worst genre's top book on main Rising Stars
            worst_genre_top_book_main_position = next(
                (b["position"] for b in main_rs_books if b["book_id"] == worst_genre_top_book["book_id"]), 
                None
            )
            
            # Find the bottom book with the same tag on main Rising Stars
            main_rs_with_tag = [b for b in main_rs_books if worst_genre_name in b["tags"]]
            main_rs_bottom_with_tag = max(main_rs_with_tag, key=lambda x: x["position"]) if main_rs_with_tag else None
            
            worst_estimate = {}
            if worst_genre_top_book_main_position and main_rs_bottom_with_tag:
                # Calculate scaling factor
                worst_genre_bottom_position = next(
                    (b["position"] for b in worst_genre_books if b["book_id"] == main_rs_bottom_with_tag["book_id"]),
                    len(worst_genre_books)  # Assume it would be at the bottom if not found
                )
                
                # Print all the intermediate findings
                logging.info(f"📊 WORST GENRE ANALYSIS:")
                logging.info(f"📊 Genre: {worst_genre_name}, Book position: #{worst_genre_position}")
                logging.info(f"📊 Top book from {worst_genre_name}: {worst_genre_top_book['title']} (ID: {worst_genre_top_book['book_id']})")
                logging.info(f"📊 Top book position on Main RS: #{worst_genre_top_book_main_position}")
                logging.info(f"📊 Bottom book from Main RS with {worst_genre_name} tag: {main_rs_bottom_with_tag['title']} (pos #{main_rs_bottom_with_tag['position']})")
                
                if worst_genre_bottom_position > worst_genre_position:
                    # Calculate how many positions between top and bottom books
                    main_rs_span = main_rs_bottom_with_tag["position"] - worst_genre_top_book_main_position
                    genre_rs_span = worst_genre_bottom_position - 1  # 1 is the top position
                    
                    # Calculate scaling factor (how many main RS positions per genre position)
                    scaling_factor = main_rs_span / genre_rs_span if genre_rs_span > 0 else 1
                    
                    # Calculate estimated positions to Main RS
                    positions_to_scale = worst_genre_position - 1  # Distance from top
                    estimated_distance = int(positions_to_scale * scaling_factor)
                    estimated_position = worst_genre_top_book_main_position + estimated_distance
                    
                    worst_estimate = {
                        "genre": worst_genre_name,
                        "book_genre_position": worst_genre_position,
                        "top_book_main_position": worst_genre_top_book_main_position,
                        "bottom_tag_book_main_position": main_rs_bottom_with_tag["position"],
                        "bottom_tag_book_genre_position": worst_genre_bottom_position,
                        "scaling_factor": scaling_factor,
                        "estimated_distance": estimated_distance,
                        "estimated_position": estimated_position,
                        "main_rs_size": len(main_rs_books),
                        "positions_away_from_bottom": max(0, len(main_rs_books) - estimated_position)
                    }
                    
                    # Log the estimate
                    logging.info(f"📉 WORST ESTIMATE: Book would be around position #{estimated_position} on Main RS")
                    logging.info(f"📉 This is {estimated_distance} positions away from the top book of {worst_genre_name}")
                    if estimated_position <= len(main_rs_books):
                        logging.info(f"📉 The book is estimated to be IN the Main Rising Stars list!")
                    else:
                        positions_away = estimated_position - len(main_rs_books)
                        logging.info(f"📉 The book is estimated to be {positions_away} positions away from joining Main Rising Stars")
                else:
                    worst_estimate = {
                        "message": f"Book is already higher than the lowest {worst_genre_name} book on Main RS",
                        "genre": worst_genre_name,
                        "book_genre_position": worst_genre_position,
                        "top_book_main_position": worst_genre_top_book_main_position,
                        "bottom_tag_book_main_position": main_rs_bottom_with_tag["position"],
                        "main_rs_size": len(main_rs_books)
                    }
            else:
                worst_estimate = {
                    "message": "Could not find enough reference books to make an estimate from worst genre",
                    "genre": worst_genre_name,
                    "book_genre_position": worst_genre_position,
                    "top_book_main_position": worst_genre_top_book_main_position,
                    "main_rs_size": len(main_rs_books)
                }
            
            estimates["worst_genre_estimate"] = worst_estimate
        
        # Create a combined estimate
        combined_estimate = {}
        if "estimated_position" in best_estimate and "estimated_position" in worst_estimate:
            # Use the average if we have both estimates
            combined_position = (best_estimate["estimated_position"] + worst_estimate["estimated_position"]) / 2
            combined_estimate = {
                "estimated_position": int(combined_position),
                "best_genre_estimate": best_estimate["estimated_position"],
                "worst_genre_estimate": worst_estimate["estimated_position"],
                "main_rs_size": len(main_rs_books)
            }
            
            if combined_position <= len(main_rs_books):
                combined_estimate["status"] = "IN_RANGE"
                combined_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at around position #{int(combined_position)}"
            else:
                positions_away = int(combined_position - len(main_rs_books))
                combined_estimate["status"] = "OUTSIDE_RANGE"
                combined_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
                combined_estimate["positions_away"] = positions_away
        elif "estimated_position" in best_estimate:
            # Only have best genre estimate
            if best_estimate["estimated_position"] <= len(main_rs_books):
                combined_estimate["status"] = "IN_RANGE"
                combined_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at around position #{best_estimate['estimated_position']}"
            else:
                positions_away = best_estimate["estimated_position"] - len(main_rs_books)
                combined_estimate["status"] = "OUTSIDE_RANGE"
                combined_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
                combined_estimate["positions_away"] = positions_away
            
            combined_estimate["estimated_position"] = best_estimate["estimated_position"]
            combined_estimate["best_genre_estimate"] = best_estimate["estimated_position"]
            combined_estimate["main_rs_size"] = len(main_rs_books)
        
        estimates["combined_estimate"] = combined_estimate
        
        return estimates
    
    except Exception as e:
        logging.exception(f"❌ Error estimating distance to main Rising Stars: {str(e)}")
        return {"error": f"Error estimating distance: {str(e)}"}


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
            
            try:
                response = fetch_with_retries(url, headers, max_retries=3)  # Reduced max retries
            except Exception as fetch_error:
                logging.warning(f"⚠️ Failed to fetch {tag} Rising Stars: {fetch_error}")
                results[tag] = f"⚠️ Failed to fetch list: {fetch_error}"
                continue

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
    book_url = request.args.get("book_url")
    estimate_distance = request.args.get("estimate_distance", "false").lower() == "true"
    
    logging.info(f"Received request for book URL: {book_url}, estimate_distance: {estimate_distance}")

    if not book_url or "royalroad.com" not in book_url:
        logging.error("❌ Invalid Royal Road URL")
        return jsonify({"error": "Invalid Royal Road URL", "results": {}, "title": "Unknown Title"}), 400

    title, book_id, tags = get_title_and_tags(book_url)

    if not book_id or not tags:
        logging.error("❌ Failed to retrieve book details")
        return jsonify({"error": "Failed to retrieve book details", "title": title, "results": {}}), 500
        
    results = check_rising_stars(book_id, tags)
    
    # Generate distance estimate if requested
    distance_estimate = {}
    if estimate_distance:
        logging.info(f"📏 Distance estimation requested for book: {title}")
        
        # Only run estimation if book is not already in main Rising Stars
        if results.get("Main Rising Stars", "").startswith("❌"):
            logging.info(f"📏 Book not in Main Rising Stars, starting estimation...")
            
            # Get headers for API requests
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.royalroad.com/",
                "DNT": "1"
            }
            
            try:
                distance_estimate = estimate_distance_to_main_rs(book_id, results, tags, headers)
                logging.info(f"📏 Distance estimation completed")
            except Exception as e:
                logging.exception(f"❌ Error during distance estimation: {str(e)}")
                distance_estimate = {"error": f"Error during estimation: {str(e)}"}
        else:
            distance_estimate = {
                "message": "Book is already in the Main Rising Stars list",
                "status": "ALREADY_IN_LIST"
            }
            logging.info(f"📏 Book already in Main Rising Stars, no estimation needed")
    
    # Build response
    response_data = {
        "title": title, 
        "results": results,
        "book_id": book_id,
        "tags": tags
    }
    
    # Add distance estimate if it was requested
    if estimate_distance:
        response_data["distance_estimate"] = distance_estimate
    
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
