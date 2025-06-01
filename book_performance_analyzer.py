from flask import Flask, request, jsonify
from flask_cors import CORS
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import random
import time
from datetime import datetime
import threading

# Enhanced logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s"
)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# User-Agent rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Base URLs
BASE_URL = "https://www.royalroad.com"
SEARCH_URL = f"{BASE_URL}/fictions/search"

def get_dynamic_spread(step, total_pages):
    """Returns a spread as a percentage of total pages."""
    return round(total_pages * 0.01 * step)

def get_scraper():
    """Creates a new cloudscraper instance with random browser settings."""
    browser_options = [
        {'browser': 'firefox', 'platform': 'windows', 'desktop': True},
        {'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        {'browser': 'chrome', 'platform': 'darwin', 'desktop': True},
    ]
    
    selected_browser = random.choice(browser_options)
    return cloudscraper.create_scraper(browser=selected_browser)

def extract_book_id(book_url):
    """Extracts the book ID from a Royal Road book URL."""
    match = re.search(r'/fiction/(\d+)', book_url)
    if match:
        return match.group(1)
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

def search_books(min_pages, max_pages, genres=None, status="ONGOING", order_by="popularity", page=1):
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

if __name__ == '__main__':
    import os
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT, debug=True)
