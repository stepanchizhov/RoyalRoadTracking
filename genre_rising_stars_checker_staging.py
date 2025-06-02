from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import random
import time
import cachetools
from datetime import datetime
import threading

# Enhanced logging
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

# User-Agent rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:112.0) Gecko/20100101 Firefox/112.0"
]

# Base URLs
BASE_URL = "https://www.royalroad.com"
SEARCH_URL = f"{BASE_URL}/fictions/search"
MAIN_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars"
GENRE_RISING_STARS_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

def get_dynamic_spread(step, total_pages):
    """Returns a spread as a percentage of total pages."""
    return round(total_pages * 0.01 * step)

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
    """Returns a random delay between 0.5-1.5 seconds."""
    return random.uniform(0.5, 1.5)

def fetch_with_retries(url, headers, max_retries=3, timeout=20):
    """Fetches a URL with retry logic."""
    for attempt in range(max_retries):
        try:
            logging.info(f"Fetching {url} (attempt {attempt + 1}/{max_retries})")
            scraper = get_scraper()
            response = scraper.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            
            if len(response.text) < 500:
                raise Exception("Response content is suspiciously short")
            
            time.sleep(0.5)
            return response
        except Exception as e:
            logging.error(f"Request failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(get_random_delay())
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
    """Helper function to process a single genre's estimate with improved scaling logic."""
    try:
        # Get all books from genre Rising Stars
        genre_books = get_books_for_genre(genre_name, headers)
        if not genre_books:
            return {"error": f"Could not fetch books for {genre_name} Rising Stars"}
        
        # Find books that appear in both lists (genre list and main RS)
        common_books = []
        for genre_book in genre_books:
            for main_book in main_rs_books:
                if genre_book["book_id"] == main_book["book_id"]:
                    common_books.append({
                        "book_id": genre_book["book_id"],
                        "title": genre_book["title"],
                        "genre_position": genre_book["position"],
                        "main_position": main_book["position"]
                    })
                    break
        
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
        common_books_by_main = sorted(common_books, key=lambda x: x["main_position"])
        
        # Get the highest and lowest books on main RS
        highest_on_main = common_books_by_main[0]
        lowest_on_main = common_books_by_main[-1]
        
        # Make sure we add the top book's main position explicitly
        top_book_main_position = highest_on_main["main_position"]
        top_book_id = highest_on_main["book_id"]
        top_book_title = highest_on_main["title"]
        
        # If we only have one common book, we'll use a simple scaling factor
        if len(common_books_by_main) <= 1 or highest_on_main["book_id"] == lowest_on_main["book_id"]:
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
        for i, book in enumerate(common_books_by_main):
            logging.info(f"📊 Common Book #{i+1}: {book['title']} - Genre #{book['genre_position']}, Main #{book['main_position']}")
        logging.info(f"📊 Calculated scaling factor: {scaling_factor:.2f}")
        logging.info(f"📊 Estimated position on Main RS: #{estimated_position}")
        
        if positions_away > 0:
            logging.info(f"📊 Book is estimated to be {positions_away} positions away from joining Main Rising Stars")
        else:
            logging.info(f"📊 Book is estimated to be IN the Main Rising Stars list!")
        
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
            "top_book_title": top_book_title,
            "highest_common_book": {
                "title": highest_on_main["title"],
                "genre_position": highest_on_main["genre_position"],
                "main_position": highest_on_main["main_position"],
                "book_id": highest_on_main["book_id"]
            },
            "lowest_common_book": {
                "title": lowest_on_main["title"],
                "genre_position": lowest_on_main["genre_position"],
                "main_position": lowest_on_main["main_position"],
                "book_id": lowest_on_main["book_id"]
            } if len(common_books_by_main) > 1 and highest_on_main["book_id"] != lowest_on_main["book_id"] else None
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
    """Creates a combined estimate from best, worst, and middle genre estimates with prioritization of the worst estimate."""
    combined_estimate = {"main_rs_size": main_rs_size}
    
    # Check if we have insufficient data message
    if "insufficient_data" in best_estimate:
        return best_estimate
    
    # Check which estimates are valid
    best_valid = "estimated_position" in best_estimate
    worst_valid = worst_estimate and "estimated_position" in worst_estimate
    middle_valid = middle_estimate and "estimated_position" in middle_estimate
    
    valid_estimates = []
    if best_valid:
        valid_estimates.append(("best", best_estimate))
    if worst_valid:
        valid_estimates.append(("worst", worst_estimate))
    if middle_valid:
        valid_estimates.append(("middle", middle_estimate))
    
    if not valid_estimates:
        # No valid estimates
        combined_estimate["status"] = "UNKNOWN"
        combined_estimate["message"] = "Could not calculate a position estimate with the available data"
        return combined_estimate
    
    # Add all estimated positions to the combined estimate for reference
    for label, estimate in valid_estimates:
        combined_estimate[f"{label}_genre_estimate"] = estimate["estimated_position"]
    
    # CHANGED: Prioritize the worst estimate (highest position number)
    # Sort by estimated position in descending order (highest/worst first)
    valid_estimates.sort(key=lambda x: x[1]["estimated_position"], reverse=True)
    selected_label, selected_estimate = valid_estimates[0]
    
    # Use the worst (highest number) estimate
    combined_estimate["estimated_position"] = selected_estimate["estimated_position"]
    combined_estimate["prioritized"] = selected_label
    
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
        avg_position = sum(estimate["estimated_position"] for _, estimate in valid_estimates) / len(valid_estimates)
        combined_estimate["average_position"] = int(round(avg_position))
        
        # Add a note about the average if it differs significantly from the prioritized estimate
        selected_position = combined_estimate["estimated_position"]
        if abs(selected_position - combined_estimate["average_position"]) > 5:
            combined_estimate["average_note"] = f"Average of all estimates is position #{combined_estimate['average_position']}"
    
    return combined_estimate
    
def estimate_distance_to_main_rs(book_id, genre_results, tags, headers):
    """
    Estimate how far the book is from the main Rising Stars list.
    Modified with minimized delays to avoid worker timeouts.
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
        
        # NEW: Check if the book is in at least two genre Rising Stars lists
        if len(book_positions) < 2:
            found_genre = list(book_positions.keys())[0]
            found_position = book_positions[found_genre]
            return {
                "message": f"Your book is currently only on the {found_genre} Rising Stars list at position #{found_position}. For a more accurate distance estimate, please check again when your book appears on at least two genre Rising Stars lists.",
                "insufficient_data": True,
                "genre": found_genre,
                "position": found_position
            }
        
        # Sort genres by the book's position (best to worst)
        sorted_genres = sorted(book_positions.items(), key=lambda x: x[1])
        
        # Find suitable genres for estimation
        suitable_genres = []
        for genre_name, genre_position in sorted_genres:
            # Process this genre to check if it's suitable
            genre_books = get_books_for_genre(genre_name, headers)
            common_books = []
            
            for genre_book in genre_books:
                for main_book in main_rs_books:
                    if genre_book["book_id"] == main_book["book_id"]:
                        common_books.append({
                            "book_id": genre_book["book_id"],
                            "genre_position": genre_book["position"],
                            "main_position": main_book["position"]
                        })
                        break
            
            # Sort by main position
            if common_books:
                common_books.sort(key=lambda x: x["main_position"])
                
                # Check if this genre has enough reference points
                if len(common_books) > 1:
                    # Check if the distance between first and last book is at least 5
                    main_distance = common_books[-1]["main_position"] - common_books[0]["main_position"]
                    if main_distance >= 5:
                        suitable_genres.append((genre_name, genre_position, common_books))
                        logging.info(f"✅ Genre {genre_name} is suitable for estimation with {len(common_books)} common books and distance {main_distance}")
                    else:
                        logging.info(f"⚠️ Genre {genre_name} has insufficient distance between reference books ({main_distance})")
                else:
                    logging.info(f"⚠️ Genre {genre_name} has only {len(common_books)} common books")
            
            # No delay here - we'll rely on network latency between requests
        
        # If we have no suitable genres, use the original sorting
        if not suitable_genres and sorted_genres:
            logging.info(f"⚠️ No suitable genres found, using original sorting")
            for genre_name, genre_position in sorted_genres[:3]:  # Try top 3 genres
                suitable_genres.append((genre_name, genre_position, []))
        
        # Process best, worst, and middle genres
        genres_to_process = []
        
        if suitable_genres:
            # Best genre (highest position/lowest number)
            genres_to_process.append(("best", suitable_genres[0][0], suitable_genres[0][1]))
            
            # Worst genre (if we have at least 2 genres)
            if len(suitable_genres) > 1:
                genres_to_process.append(("worst", suitable_genres[-1][0], suitable_genres[-1][1]))
            
            # Middle genre (if we have at least 3 genres)
            if len(suitable_genres) >= 3:
                middle_index = len(suitable_genres) // 2
                genres_to_process.append(("middle", suitable_genres[middle_index][0], suitable_genres[middle_index][1]))
        else:
            # Fallback to original sorting if no suitable genres
            if sorted_genres:
                genres_to_process.append(("best", sorted_genres[0][0], sorted_genres[0][1]))
                if len(sorted_genres) > 1:
                    genres_to_process.append(("worst", sorted_genres[-1][0], sorted_genres[-1][1]))
                if len(sorted_genres) >= 3:
                    middle_index = len(sorted_genres) // 2
                    genres_to_process.append(("middle", sorted_genres[middle_index][0], sorted_genres[middle_index][1]))
        
        # Process each selected genre
        for label, genre_name, genre_position in genres_to_process:
            logging.info(f"🔍 Processing {label} genre: {genre_name} at position #{genre_position}")
            genre_estimate = process_genre_estimate(genre_name, genre_position, main_rs_books, headers)
            estimates[f"{label}_genre_estimate"] = genre_estimate
            
            # No explicit delay here - rely on network latency
        
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

def parse_book_stats(soup):
    """Extracts statistics from a book page."""
    stats = {
        'followers': 0,
        'favorites': 0,
        'views': 0,
        'avg_views': 0,
        'ratings': 0,
        'rating_score': None,
        'pages': 0,
        'chapters': 0,
        'title': 'Unknown',
        'author': 'Unknown',
        'genres': []
    }
    
    # Extract title
    title_tag = soup.find("h1", class_="font-white")
    if title_tag:
        stats['title'] = title_tag.text.strip()
    
    # Extract author
    author_tag = soup.find("h4", class_="font-white")
    if author_tag:
        author_link = author_tag.find("a")
        if author_link:
            stats['author'] = author_link.text.strip()
    
    # Extract genres/tags
    fiction_tags = soup.find_all("a", class_="fiction-tag")
    for tag in fiction_tags:
        tag_text = tag.text.strip()
        if tag_text:
            stats['genres'].append(tag_text)
    
    # Extract statistics
    stats_section = soup.find("div", class_="fiction-stats")
    if stats_section:
        # Look for specific stats
        for li in stats_section.find_all("li"):
            text = li.text.strip()
            
            if "Followers :" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    followers_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['followers'] = int(followers_text)
                    except:
                        pass
                        
            elif "Favorites :" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    favorites_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['favorites'] = int(favorites_text)
                    except:
                        pass
                        
            elif "Total Views :" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    views_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['views'] = int(views_text)
                    except:
                        pass
                        
            elif "Average Views :" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    avg_views_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['avg_views'] = int(avg_views_text)
                    except:
                        pass
                        
            elif "Ratings :" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    ratings_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['ratings'] = int(ratings_text)
                    except:
                        pass
                        
            elif "Pages" in text:
                next_li = li.find_next_sibling("li")
                if next_li:
                    pages_text = next_li.text.strip().replace(",", "")
                    try:
                        stats['pages'] = int(pages_text)
                    except:
                        pass
    
    # Extract rating score
    overall_score = soup.find("li", string=re.compile("Overall Score"))
    if overall_score:
        score_element = overall_score.find_next_sibling("li")
        if score_element:
            star_element = score_element.find("span", class_="star")
            if star_element and star_element.get("data-content"):
                match = re.search(r'([\d.]+)\s*/\s*5', star_element.get("data-content"))
                if match:
                    stats['rating_score'] = float(match.group(1))
    
    # Count chapters
    chapters_table = soup.find("table", id="chapters")
    if chapters_table:
        tbody = chapters_table.find("tbody")
        if tbody:
            stats['chapters'] = len(tbody.find_all("tr"))
    
    return stats

def get_book_data(book_id):
    """Fetches and parses data for a specific book."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    url = f"{BASE_URL}/fiction/{book_id}/"
    
    try:
        response = fetch_with_retries(url, headers)
        soup = BeautifulSoup(response.text, "html.parser")
        stats = parse_book_stats(soup)
        stats['book_id'] = book_id
        return stats
    except Exception as e:
        logging.error(f"Error fetching book {book_id}: {e}")
        return None

def search_books(min_pages, max_pages, genres=None, status="ONGOING", order_by="followers", page=1):
    """Searches for books with specified criteria."""
    params = {
        'globalFilters': 'false',
        'minPages': min_pages,
        'maxPages': max_pages,
        'status': status,
        'orderBy': order_by,
        'page': page
    }
    
    # Add genres if specified
    if genres:
        # RoyalRoad uses multiple tagsAdd parameters for genres
        params_list = []
        for key, value in params.items():
            params_list.append(f"{key}={value}")
        
        for genre in genres:
            params_list.append(f"tagsAdd={genre}")
        
        url = f"{SEARCH_URL}?{'&'.join(params_list)}"
    else:
        url = f"{SEARCH_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    try:
        response = fetch_with_retries(url, headers)
        soup = BeautifulSoup(response.text, "html.parser")
        
        books = []
        fiction_items = soup.find_all("div", class_="fiction-list-item")
        
        for item in fiction_items:
            book_data = {}
            
            # Extract book ID and URL
            title_link = item.find("a", class_="font-red-sunglo")
            if title_link and title_link.get("href"):
                book_id = extract_book_id(title_link["href"])
                if book_id:
                    book_data['book_id'] = book_id
                    book_data['title'] = title_link.text.strip()
                    
                    # Extract basic stats from search results
                    stats_divs = item.find_all("div", class_="col-sm-6")
                    for div in stats_divs:
                        text = div.text.strip()
                        if "Followers" in text:
                            match = re.search(r'(\d+)', text)
                            if match:
                                book_data['followers'] = int(match.group(1))
                        elif "Pages" in text:
                            match = re.search(r'(\d+)', text)
                            if match:
                                book_data['pages'] = int(match.group(1))
                        elif "Chapters" in text:
                            match = re.search(r'(\d+)', text)
                            if match:
                                book_data['chapters'] = int(match.group(1))
                    
                    books.append(book_data)
        
        # Check if there are more pages
        pagination = soup.find("ul", class_="pagination")
        has_next = False
        if pagination:
            next_link = pagination.find("a", string=re.compile("Next"))
            has_next = next_link is not None
        
        return books, has_next
        
    except Exception as e:
        logging.error(f"Error searching books: {e}")
        return [], False

def find_similar_books(target_pages, target_genres=None, required_count=None, min_chapters=2):
    if required_count is None:
        required_count = 20

    books = []
    page_range = 0
    max_range_attempts = 100

    while len(books) < required_count and page_range <= max_range_attempts:
        spread = get_dynamic_spread(page_range, target_pages)
        min_pages = max(1, target_pages - spread)
        max_pages = target_pages + spread
        logging.info(f"Spread step {page_range} ({spread} pages) → range: {min_pages}–{max_pages}")

        page = 1
        has_next = True

        while has_next and len(books) < required_count:
            found_books, has_next = search_books(
                min_pages=min_pages,
                max_pages=max_pages,
                genres=target_genres,
                page=page
            )

            for book in found_books:
                if book.get('chapters', 0) >= min_chapters:
                    if book['book_id'] not in {b['book_id'] for b in books}:
                        books.append(book)
                        if len(books) >= required_count:
                            break

            page += 1
            time.sleep(get_random_delay())

        page_range += 1

    logging.info(f"Found {len(books)} books in total.")
    return books[:required_count]

def calculate_percentiles(target_stats, comparison_stats):
    """Calculates percentile rankings for the target book."""
    metrics = {}
    
    # Define which metrics to compare
    metric_names = {
        'followers': 'Followers',
        'views': 'Total Views',
        'avg_views': 'Average Views',
        'favorites': 'Favorites',
        'ratings': 'Rating Count',
        'rating_score': 'Rating Score'
    }
    
    for metric_key, metric_name in metric_names.items():
        target_value = target_stats.get(metric_key)
        
        if target_value is not None:
            # Get all comparison values for this metric
            comparison_values = [
                book.get(metric_key) for book in comparison_stats 
                if book.get(metric_key) is not None
            ]
            
            if comparison_values:
                # Calculate percentile
                below_count = sum(1 for v in comparison_values if v < target_value)
                percentile = (below_count / len(comparison_values)) * 100
                
                metrics[metric_key] = {
                    'value': target_value,
                    'percentile': round(percentile, 1),
                    'better_than': round(percentile, 1),
                    'comparison_count': len(comparison_values)
                }
    
    # Calculate ratios
    if target_stats.get('pages') and target_stats.get('followers'):
        followers_per_page = target_stats['followers'] / target_stats['pages']
        
        # Compare with others
        comparison_ratios = []
        for book in comparison_stats:
            if book.get('pages') and book.get('followers'):
                ratio = book['followers'] / book['pages']
                comparison_ratios.append(ratio)
        
        if comparison_ratios:
            below_count = sum(1 for r in comparison_ratios if r < followers_per_page)
            percentile = (below_count / len(comparison_ratios)) * 100
            
            metrics['followers_per_page'] = {
                'value': round(followers_per_page, 2),
                'percentile': round(percentile, 1),
                'better_than': round(percentile, 1)
            }
    
    return metrics

def analyze_book_for_rising_stars_streaming(book_url):
    # Step 1
    yield "Fetching book data..."
    book_data = fetch_book_data(book_url)

    # Step 2
    yield "Finding similar books..."
    similar_books = find_similar_books(book_data)

    # Step 3
    total = len(similar_books)
    for i, book in enumerate(similar_books):
        # Do something...
        yield f"Processing book {i + 1}/{total}"

    # Final
    yield "Calculating performance metrics..."
    result = calculate_performance(book_data, similar_books)

    yield f"RESULT: {json.dumps(result)}"

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
            # Joking message when already on main list
            distance_estimate = {
                "message": f"Hey! Why are you wasting precious energy? Your book is already on the main Rising Stars list at position #{main_rs_position}! 🎉",
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
                        distance_estimate = estimate_distance_to_main_rs(book_id, final_results, tags, headers)
                        # If it's not an error and not "insufficient_data", cache it
                        if "error" not in distance_estimate and "insufficient_data" not in distance_estimate:
                            with cache_lock:
                                cache[cache_key_distance] = distance_estimate
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

@app.route('/analyze_book', methods=['GET'])
def analyze_book():
    """Main endpoint for analyzing book performance."""
    start_time = time.time()
    
    book_url = request.args.get('book_url', '').strip()
    comparison_size = int(request.args.get('comparison_size', 20))
    min_chapters = int(request.args.get('min_chapters', 2))
    genres = request.args.getlist('genres')
    
    # Extract book ID
    book_id = extract_book_id(book_url)
    if not book_id:
        return jsonify({
            'error': 'Invalid Royal Road URL'
        }), 400
    
    try:
        # Get target book data
        target_book = get_book_data(book_id)
        if not target_book:
            return jsonify({
                'error': 'Failed to fetch book data'
            }), 500
        
        # Use book's genres if none specified
        if not genres or 'all' in genres:
            genres = None
        elif 'same' in genres:
            # Convert display genres to search tags
            genre_mapping = {
                'Action': 'action',
                'Adventure': 'adventure',
                'Comedy': 'comedy',
                'Drama': 'drama',
                'Fantasy': 'fantasy',
                'Horror': 'horror',
                'Mystery': 'mystery',
                'Psychological': 'psychological',
                'Romance': 'romance',
                'Sci-fi': 'sci_fi',
                'LitRPG': 'litrpg',
                'Portal Fantasy / Isekai': 'summoned_hero',
                'Progression': 'progression',
                'Male Lead': 'male_lead',
                'Female Lead': 'female_lead',
                'Strong Lead': 'strong_lead',
                'Magic': 'magic',
                'Martial Arts': 'martial_arts',
                'Slice of Life': 'slice_of_life',
                'Supernatural': 'supernatural',
                'School Life': 'school_life',
                'Reincarnation': 'reincarnation',
                'Harem': 'harem',
                'GameLit': 'gamelit',
                'Grimdark': 'grimdark',
                'Villainous Lead': 'villainous_lead',
                'High Fantasy': 'high_fantasy',
                'Low Fantasy': 'low_fantasy',
                'Urban Fantasy': 'urban_fantasy',
                'Wuxia': 'wuxia',
                'Xianxia': 'xianxia',
                'Mythos': 'mythos',
                'Satire': 'satire',
                'Tragedy': 'tragedy',
                'Short Story': 'one_shot',
                'Contemporary': 'contemporary',
                'Historical': 'historical',
                'Non-Human Lead': 'non-human_lead',
                'Anti-Hero Lead': 'anti-hero_lead',
                'Time Travel': 'time_travel',
                'Post Apocalyptic': 'post_apocalyptic',
                'Soft Sci-fi': 'soft_sci-fi',
                'Hard Sci-fi': 'hard_sci-fi',
                'Space Opera': 'space_opera',
                'War and Military': 'war_and_military',
                'Steampunk': 'steampunk',
                'Cyberpunk': 'cyberpunk',
                'Dystopia': 'dystopia',
                'Virtual Reality': 'virtual_reality',
                'Artificial Intelligence': 'artificial_intelligence',
                'Time Loop': 'loop',
                'Ruling Class': 'ruling_class',
                'Dungeon': 'dungeon',
                'Sports': 'sports',
                'Technologically Engineered': 'technologically_engineered',
                'Genetically Engineered': 'genetically_engineered ',
                'Super Heroes': 'super_heroes',
                'Multiple Lead Characters': 'multiple_lead',
                'Strategy': 'strategy',
                'First Contact': 'first_contact',
                'Attractive Lead': 'attractive_lead',
                'Gender Bender': 'gender_bender',
                'Reader Interactive': 'reader_interactive',
                'Secret Identity': 'secret_identity'
            }
            
            genres = []
            for genre in target_book.get('genres', []):
                mapped = genre_mapping.get(genre)
                if mapped:
                    genres.append(mapped)
        
        # Find similar books
        similar_books = find_similar_books(
            target_pages=target_book['pages'],
            target_genres=genres,
            required_count=comparison_size,
            min_chapters=min_chapters
        )
        
        # Fetch detailed data for comparison books
        comparison_data = []
        
        for i, book in enumerate(similar_books):
            if i % 10 == 0:
                logging.info(f"Processing book {i+1}/{len(similar_books)}")
            
            book_data = get_book_data(book['book_id'])
            if book_data:
                comparison_data.append(book_data)
            
            # Add delay to avoid rate limiting
            time.sleep(get_random_delay())
        
        # Calculate metrics
        metrics = calculate_percentiles(target_book, comparison_data)
        
        # Prepare response
        response_data = {
            'target_book': target_book,
            'metrics': metrics,
            'comparison_count': len(comparison_data),
            'comparison_criteria': {
                'genres': genres,
                'min_chapters': min_chapters,
                'pages': target_book['pages']
            },
            'processing_time': f"{time.time() - start_time:.2f} seconds"
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logging.exception(f"Error analyzing book: {e}")
        return jsonify({
            'error': f'Error analyzing book: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'time': datetime.now().isoformat()
    })

@app.route('/progress_stream')
def progress_stream():
    def generate():
        for i in range(1, 101):
            yield f"data: Processing book {i}/100\n\n"
            time.sleep(0.1)  # Simulate delay
        yield "data: done\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route("/stream_analyze_book")
def stream_analyze_book():
    def generate():
        yield "data: Connecting...\n\n"
        for i in range(1, 101):
            yield f"data: Processing book {i}/100\n\n"
            time.sleep(0.1)
        yield "data: done\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
#def stream_analyze_book():
#    book_url = request.args.get("book_url")
#    comparison_size = int(request.args.get("comparison_size", 20))
#    min_chapters = int(request.args.get("min_chapters", 5))
#    genres = request.args.getlist("genres")
#
#    def event_stream():
#        try:
#            for update in analyze_book_streaming(book_url, comparison_size, min_chapters, genres):
#                yield f"data: {update}\n\n"
#        except Exception as e:
#            yield f"data: ERROR: {str(e)}\n\n"
#
#    return Response(stream_with_context(event_stream()), mimetype='text/event-stream')


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
