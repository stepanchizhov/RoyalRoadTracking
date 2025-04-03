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
            
            href = title_link.get("href", "")
            if not href or "/" not in href:
                continue
                
            try:
                book_id = href.split("/")[2]
                # Extract book title
                title = title_link.text.strip()
                
                genre_books.append({
                    "position": position,
                    "book_id": book_id,
                    "title": title
                })
            except (IndexError, ValueError) as e:
                logging.warning(f"⚠️ Could not parse book link {href}: {e}")
                continue
            
        # Cache the results
        with cache_lock:
            cache[cache_key] = genre_books
            
        return genre_books
    
    except Exception as e:
        logging.exception(f"❌ Error fetching genre Rising Stars books: {str(e)}")
        return []

def process_genre_estimate(genre_name, genre_position, main_rs_books, headers):
    """
    Helper function to process a single genre's estimate with improved scaling logic.
    Optimized for speed and accuracy.
    """
    try:
        # Check cache first for genre books
        cache_key = f"genre_books_{genre_name}"
        with cache_lock:
            if cache_key in cache:
                genre_books = cache[cache_key]
                logging.info(f"📋 Cache hit for genre books: {genre_name}")
            else:
                # Get all books from genre Rising Stars
                genre_books = get_books_for_genre(genre_name, headers)
                
                # Cache the result if successful
                if genre_books:
                    with cache_lock:
                        cache[cache_key] = genre_books
        
        if not genre_books:
            return {"error": f"Could not fetch books for {genre_name} Rising Stars"}
        
        # Find books that appear in both lists (genre list and main RS)
        common_books = []
        genre_book_ids = {book["book_id"]: book for book in genre_books}
        main_book_ids = {book["book_id"]: book for book in main_rs_books}
        
        # More efficient intersection using dictionary lookups
        for book_id in set(genre_book_ids.keys()) & set(main_book_ids.keys()):
            common_books.append({
                "book_id": book_id,
                "title": genre_book_ids[book_id]["title"],
                "genre_position": genre_book_ids[book_id]["position"],
                "main_position": main_book_ids[book_id]["position"]
            })
        
        # If we don't have any common books, we can't make an estimate
        if not common_books:
            return {
                "message": f"No common books found between {genre_name} Rising Stars and Main Rising Stars",
                "genre": genre_name,
                "book_genre_position": genre_position,
                "main_rs_size": len(main_rs_books),
                "top_book_main_position": None,
                "top_book_id": None,
                "top_book_title": None
            }
        
        # Sort by main position (lowest to highest)
        common_books.sort(key=lambda x: x["main_position"])
        
        # Get the highest and lowest books on main RS
        highest_on_main = common_books[0]
        lowest_on_main = common_books[-1]
        
        # Make sure we add the top book's main position explicitly
        top_book_main_position = highest_on_main["main_position"]
        top_book_id = highest_on_main["book_id"]
        top_book_title = highest_on_main["title"]
        
        # If we only have one common book, we'll use a simple scaling factor
        if len(common_books) <= 1 or highest_on_main["book_id"] == lowest_on_main["book_id"]:
            # We only have one reference point
            reference_book = highest_on_main
            
            # Calculate distance from reference book in genre list
            genre_distance = abs(genre_position - reference_book["genre_position"])
            
            # Use the position of the reference book as base for our estimate
            if genre_position < reference_book["genre_position"]:
                # Our book is higher in the genre list
                estimated_position = max(1, reference_book["main_position"] - genre_distance)
            else:
                # Our book is lower in the genre list
                estimated_position = reference_book["main_position"] + genre_distance
            
            scaling_factor = 1.0  # Default scaling when we only have one reference
            
        else:
            # Calculate scaling factor using the highest and lowest books
            main_distance = lowest_on_main["main_position"] - highest_on_main["main_position"]
            genre_distance = abs(lowest_on_main["genre_position"] - highest_on_main["genre_position"])
            
            # Prevent division by zero
            if genre_distance == 0:
                genre_distance = 1
                
            scaling_factor = main_distance / genre_distance
            
            # Calculate our position relative to the highest book on the main list
            genre_distance_from_highest = abs(genre_position - highest_on_main["genre_position"])
            
            # Apply the scaling factor to get estimated main list distance
            if genre_position < highest_on_main["genre_position"]:
                # Our book is higher in the genre list
                estimated_position = max(1, highest_on_main["main_position"] - (genre_distance_from_highest * scaling_factor))
            else:
                # Our book is lower in the genre list
                estimated_position = highest_on_main["main_position"] + (genre_distance_from_highest * scaling_factor)
        
        # Round to nearest integer
        estimated_position = int(round(estimated_position))
        
        # Calculate positions away from joining main RS
        positions_away = max(0, estimated_position - len(main_rs_books))
        
        # Log the analysis
        logging.info(f"📊 DETAILED GENRE ANALYSIS: {genre_name}")
        logging.info(f"📊 Book position in {genre_name}: #{genre_position}")
        logging.info(f"📊 Found {len(common_books)} common books between {genre_name} and Main Rising Stars")
        logging.info(f"📊 Calculated scaling factor: {scaling_factor:.2f}")
        logging.info(f"📊 Estimated position on Main RS: #{estimated_position}")
        
        # Build the result
        genre_estimate = {
            "genre": genre_name,
            "book_genre_position": genre_position,
            "scaling_factor": scaling_factor,
            "estimated_position": estimated_position,
            "main_rs_size": len(main_rs_books),
            "common_books_count": len(common_books),
            "top_book_main_position": top_book_main_position,
            "top_book_id": top_book_id,
            "top_book_title": top_book_title
        }
        
        # Add status information
        if estimated_position <= len(main_rs_books):
            genre_estimate["status"] = "IN_RANGE"
            genre_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at position #{estimated_position}"
        else:
            genre_estimate["status"] = "OUTSIDE_RANGE"
            genre_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
            genre_estimate["positions_away"] = positions_away
        
        return genre_estimate
        
    except Exception as e:
        logging.exception(f"❌ Error processing genre estimate for {genre_name}: {str(e)}")
        return {
            "error": f"Error processing genre: {str(e)}",
            "genre": genre_name,
            "book_genre_position": genre_position,
            "top_book_main_position": None,
            "top_book_id": None,
            "top_book_title": None
        }
        
def create_combined_estimate(best_estimate, worst_estimate, middle_estimate, main_rs_size):
    """
    Creates a combined estimate from selected genre estimates.
    Prioritizes estimates based on reliability indicators.
    """
    combined_estimate = {"main_rs_size": main_rs_size}
    
    # Check which estimates are valid
    best_valid = best_estimate and "estimated_position" in best_estimate
    worst_valid = worst_estimate and "estimated_position" in worst_estimate
    middle_valid = middle_estimate and "estimated_position" in middle_estimate
    
    valid_estimates = []
    if best_valid:
        valid_estimates.append((
            "best", 
            best_estimate, 
            best_estimate.get("common_books_count", 0)
        ))
    if worst_valid:
        valid_estimates.append((
            "worst", 
            worst_estimate, 
            worst_estimate.get("common_books_count", 0)
        ))
    if middle_valid:
        valid_estimates.append((
            "middle", 
            middle_estimate, 
            middle_estimate.get("common_books_count", 0)
        ))
    
    if not valid_estimates:
        # No valid estimates
        combined_estimate["status"] = "UNKNOWN"
        combined_estimate["message"] = "Could not calculate a position estimate with the available data"
        return combined_estimate
    
    # Add all estimated positions to the combined estimate for reference
    for label, estimate, _ in valid_estimates:
        combined_estimate[f"{label}_genre_estimate"] = estimate["estimated_position"]
    
    # First prioritize by number of common books (more is better)
    valid_estimates.sort(key=lambda x: x[2], reverse=True)
    
    # If the top two have the same number of common books, choose the worst estimate
    if len(valid_estimates) >= 2 and valid_estimates[0][2] == valid_estimates[1][2]:
        # Sort by estimated position in descending order (highest/worst first)
        valid_estimates.sort(key=lambda x: x[1]["estimated_position"], reverse=True)
    
    selected_label, selected_estimate, _ = valid_estimates[0]
    
    # Use the selected estimate
    combined_estimate["estimated_position"] = selected_estimate["estimated_position"]
    combined_estimate["prioritized"] = selected_label
    combined_estimate["prioritized_reason"] = "most reference books" if selected_label == "best" else "worst position"
    
    # Determine if the book is expected to be in range
    if selected_estimate["estimated_position"] <= main_rs_size:
        combined_estimate["status"] = "IN_RANGE"
        combined_estimate["message"] = f"Book is estimated to be in the Main Rising Stars at around position #{selected_estimate['estimated_position']}"
    else:
        positions_away = selected_estimate["estimated_position"] - main_rs_size
        combined_estimate["status"] = "OUTSIDE_RANGE"
        combined_estimate["positions_away"] = positions_away
        combined_estimate["message"] = f"Book is estimated to be {positions_away} positions away from joining Main Rising Stars"
    
    # If we have multiple estimates, calculate an average as well
    if len(valid_estimates) > 1:
        avg_position = sum(estimate["estimated_position"] for _, estimate, _ in valid_estimates) / len(valid_estimates)
        combined_estimate["average_position"] = int(round(avg_position))
        
        # Add a note about the average if it differs significantly from the prioritized estimate
        selected_position = combined_estimate["estimated_position"]
        if abs(selected_position - combined_estimate["average_position"]) > 5:
            combined_estimate["average_note"] = f"Average of all estimates is position #{combined_estimate['average_position']}"
    
    return combined_estimate
    
def estimate_distance_to_main_rs(book_id, genre_results, tags, headers):
    """
    Estimate how far the book is from the main Rising Stars list.
    Optimized to selectively process only the most relevant genres.
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
            if status.startswith("✅ Found in position #") and genre != "Main Rising Stars":
                position = int(re.search(r"#(\d+)", status).group(1))
                book_positions[genre] = position
        
        if not book_positions:
            return {"message": "Book not found in any genre Rising Stars lists, cannot estimate distance"}
        
        # Sort genres by the book's position (best to worst)
        sorted_genres = sorted(book_positions.items(), key=lambda x: x[1])
        
        # Select only a subset of genres to process
        # - Best position genre (always include)
        # - Worst position genre (if different from best)
        # - Middle position genre (if we have at least 3 genres)
        
        genres_to_process = []
        
        # Always include the best genre (lowest position number)
        if sorted_genres:
            best_genre, best_position = sorted_genres[0]
            genres_to_process.append(("best", best_genre, best_position))
            logging.info(f"🎯 Selected best genre for estimation: {best_genre} at position #{best_position}")
        
        # Include worst genre if we have at least 2 genres and it's different from best
        if len(sorted_genres) > 1:
            worst_genre, worst_position = sorted_genres[-1]
            if worst_genre != best_genre:
                genres_to_process.append(("worst", worst_genre, worst_position))
                logging.info(f"🎯 Selected worst genre for estimation: {worst_genre} at position #{worst_position}")
        
        # Include middle genre if we have at least 3 genres
        if len(sorted_genres) >= 3:
            middle_index = len(sorted_genres) // 2
            middle_genre, middle_position = sorted_genres[middle_index]
            if middle_genre != best_genre and middle_genre != worst_genre:
                genres_to_process.append(("middle", middle_genre, middle_position))
                logging.info(f"🎯 Selected middle genre for estimation: {middle_genre} at position #{middle_position}")
        
        logging.info(f"📊 Selected {len(genres_to_process)} genres for distance estimation out of {len(sorted_genres)} available")
        
        # Process each selected genre
        for label, genre_name, genre_position in genres_to_process:
            logging.info(f"🔍 Processing {label} genre: {genre_name} at position #{genre_position}")
            genre_estimate = process_genre_estimate(genre_name, genre_position, main_rs_books, headers)
            estimates[f"{label}_genre_estimate"] = genre_estimate
        
        # Create a combined estimate
        combined_estimate = create_combined_estimate(
            estimates.get("best_genre_estimate", {}),
            estimates.get("worst_genre_estimate", {}),
            estimates.get("middle_genre_estimate", {}),
            len(main_rs_books)
        )
        
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
    logging.info(f"🔍 [{request_id}] Received book_url: {book_url}")
    logging.info(f"🔍 [{request_id}] Estimate distance: {estimate_distance_param}")

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
    
    # Final results container
    final_results = {}
    
    # Check if we have cached results for main rising stars
    cache_key_main_result = f"main_rs_result_{book_id}"
    with cache_lock:
        if cache_key_main_result in cache:
            final_results["Main Rising Stars"] = cache[cache_key_main_result]
            logging.info(f"📋 [{request_id}] Cache hit for main rising stars result: {book_id}")
        else:
            # Fetch main rising stars result
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
    
    # Process genre results - we'll always check all genres to provide complete results
    for tag in tags:
        cache_key_genre = f"genre_result_{book_id}_{tag}"
        with cache_lock:
            if cache_key_genre in cache:
                final_results[tag] = cache[cache_key_genre]
                logging.info(f"📋 [{request_id}] Cache hit for genre result: {tag}")
                continue
        
        try:
            url = f"{GENRE_RISING_STARS_URL}{tag}"
            logging.info(f"🔍 [{request_id}] Checking Rising Stars for genre: {tag}")
            
            response = fetch_with_retries(url, headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            book_links = soup.find_all("a", class_="font-red-sunglo")
            book_ids = [link.get('href', '').split('/')[2] for link in book_links if link.get('href', '')]

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
    
    # Check if the book is already on the main Rising Stars list
    already_on_main_rs = False
    main_rs_position = None
    
    if "Main Rising Stars" in final_results and "✅ Found in position #" in final_results["Main Rising Stars"]:
        already_on_main_rs = True
        position_match = re.search(r"#(\d+)", final_results["Main Rising Stars"])
        if position_match:
            main_rs_position = int(position_match.group(1))
    
    # Distance estimation logic with improved error handling
    distance_estimate = {}
    if estimate_distance_param:
        if already_on_main_rs:
            # Skip estimation if already on main list
            distance_estimate = {
                "message": f"Your book is already on the main Rising Stars list at position #{main_rs_position}! 🎉",
                "already_on_main": True,
                "main_position": main_rs_position
            }
            logging.info(f"🎯 [{request_id}] Book already on main Rising Stars at position #{main_rs_position}")
        else:
            try:
                cache_key_distance = f"distance_estimate_{book_id}"
                with cache_lock:
                    if cache_key_distance in cache:
                        distance_estimate = cache[cache_key_distance]
                        logging.info(f"📋 [{request_id}] Cache hit for distance estimate: {book_id}")
                    else:
                        # Use our optimized estimation function
                        distance_estimate = estimate_distance_to_main_rs(book_id, final_results, tags, headers)
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
