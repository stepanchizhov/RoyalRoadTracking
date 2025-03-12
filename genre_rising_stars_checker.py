import time
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

BASE_URL = "https://www.royalroad.com/fictions/rising-stars?genre="

def get_tags_from_book(url):
    """Extracts tags from a Royal Road book page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract book ID from URL
        book_id = url.split("/")[-2]  # Get the numerical book ID from URL

        # Extract tags from the book's page
        tags = [tag["href"].split("tagsAdd=")[-1] for tag in soup.find_all("a", class_="fiction-tag")]

        return book_id, tags

    except requests.RequestException as e:
        return None, None

def check_rising_stars(book_id, tags):
    """Checks if the book is listed in Rising Stars under relevant tags with a 5-second delay."""
    results = {}

    for tag in tags:
        url = f"{BASE_URL}{tag}"
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Find all highlighted book IDs in the Rising Stars list
            titles = [a["href"].split("/")[2] for a in soup.find_all('a', class_='font-red-sunglo bold')]

            # Check if the book is present and find its ranking position
            if book_id in titles:
                position = titles.index(book_id) + 1
