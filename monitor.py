import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


SHOP_URL = (
    "https://fril.jp/shop/"
    "c04053ea97f5d2af8919e4ddae1fc4e0"
)

STATE_FILE = "seen_items.json"

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {"seen": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )


def normalize_url(url):
    if not url:
        return None

    url = urljoin("https://fril.jp/", url.strip())

    # Remove query string and fragment.
    url = url.split("?", 1)[0]
    url = url.split("#", 1)[0]

    return url


def fetch(url):
    print(f"Fetching: {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.text


def extract_listings(html):
    """
    Extract Rakuma item links from a seller page.

    The current seller page exposes item.fril.jp URLs and
    displays SOLD OUT next to sold listings.
    """

    soup = BeautifulSoup(html, "html.parser")

    # Group all occurrences of the same item URL.
    items = {}

    for link in soup.find_all("a", href=True):

        href = normalize_url(link["href"])

        if not href:
            continue

        parsed = urlparse(href)

        if parsed.netloc.lower() != "item.fril.jp":
            continue

        if not parsed.path or parsed.path == "/":
            continue

        if href not in items:
            items[href] = {
                "url": href,
                "title": "",
                "price": "",
                "sold_out": False,
            }

        text = link.get_text(" ", strip=True)

        if "SOLD OUT" in text.upper():
            items[href]["sold_out"] = True

        elif text and not items[href]["title"]:
            items[href]["title"] = text

    # Try to get price and better title from surrounding cards.
    for href, item in items.items():

        # Find an anchor pointing to this exact item.
        anchor = soup.find(
            "a",
            href=lambda value: (
                value
                and normalize_url(value) == href
            ),
        )

        if not anchor:
            continue

        # Walk up a few levels looking for a product card.
        containers = []

        current = anchor

        for _ in range(5):
            if current is None:
                break

            containers.append(current)
            current = current.parent

        for container in containers:

            text = container.get_text(
                " ",
                strip=True,
            )

            if not text:
                continue

            # Detect sold status anywhere in the card.
            if "SOLD OUT" in text.upper():
                item["sold_out"] = True

            # Find Japanese yen price.
            price_match = re.search(
                r"¥\s*[\d,]+",
                text,
            )

            if price_match and not item["price"]:
                item["price"] = price_match.group(0)

            # Stop once we've found enough information.
            if item["title"] and item["price"]:
                break

    return list(items.values())


def get_active_listings():
    """
    Get all listings currently visible on the seller page.

    We intentionally do not crawl arbitrary links. We only
    inspect item.fril.jp links belonging to the seller page.
    """

    html = fetch(SHOP_URL)

    listings = extract_listings(html)

    active = [
        item
        for item in listings
        if not item["sold_out"]
    ]

    print(
        f"Found {len(listings)} total item URLs."
    )

    print(
        f"Found {len(active)} active listings."
    )

    for item in active:
        print(
            f"ACTIVE: {item['title']} "
            f"{item['price']} "
            f"{item['url']}"
        )

    return active


def send_discord(listing):
    title = listing["title"] or "New Rakuma listing"
    price = listing["price"] or "Price unavailable"
    url = listing["url"]

    payload = {
        "username": "Rakuma Monitor",
        "embeds": [
            {
                "title": "🚨 New Rakuma Listing",
                "description": title,
                "color": 0x00C853,
                "fields": [
                    {
                        "name": "Price",
                        "value": price,
                        "inline": True,
                    },
                    {
                        "name": "Seller",
                        "value": "ねぎとろ",
                        "inline": True,
                    },
                ],
                "url": url,
                "footer": {
                    "text": "Rakuma seller monitor"
                },
            }
        ],
        "allowed_mentions": {
            "parse": []
        },
    }

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    print("Discord notification sent.")


def main():
    print("=" * 60)
    print("Rakuma seller monitor")
    print("=" * 60)

    listings = get_active_listings()

    state = load_state()

    seen = set(state.get("seen", []))

    # ---------------------------------------------------------
    # FIRST RUN
    # ---------------------------------------------------------
    #
    # Do not alert for listings that already exist.
    #
    if not seen:

        print(
            "No previous state found."
        )

        print(
            "Creating initial baseline without notifications."
        )

        state["seen"] = [
            item["url"]
            for item in listings
        ]

        save_state(state)

        print(
            f"Baseline saved: {len(listings)} listings."
        )

        return

    # ---------------------------------------------------------
    # FIND NEW ACTIVE LISTINGS
    # ---------------------------------------------------------

    new_listings = [
        item
        for item in listings
        if item["url"] not in seen
    ]

    if not new_listings:

        print("No new listings.")

        return

    print(
        f"🚨 {len(new_listings)} new listing(s) found!"
    )

    # ---------------------------------------------------------
    # SEND NOTIFICATIONS
    # ---------------------------------------------------------

    for listing in new_listings:

        print(
            f"New listing: "
            f"{listing['title']} "
            f"{listing['url']}"
        )

        send_discord(listing)

    # ---------------------------------------------------------
    # UPDATE STATE
    # ---------------------------------------------------------

    for listing in new_listings:
        seen.add(listing["url"])

    state["seen"] = sorted(seen)

    save_state(state)

    print("State saved.")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            f"ERROR: {type(exc).__name__}: {exc}"
        )

        sys.exit(1)
