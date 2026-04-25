"""
scraper.py — Downloads a product page and extracts the price + currency.

IMPROVEMENTS OVER V1:
- Tries 4 extraction strategies in priority order (best → fallback)
- Reads JSON-LD structured data (most reliable, used by big stores)
- Reads <meta> price tags (used by many modern e-commerce sites)
- Smarter CSS selector matching (avoids grabbing crossed-out old prices)
- Now returns BOTH price (float) AND currency (str), e.g. (19.99, "USD")
- Retry logic: tries up to 3 times before giving up
- Small delay between retries to avoid being blocked
- Better logging so you can see exactly what happened
"""

import re
import json
import asyncio
import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# How many times to retry a failed request
MAX_RETRIES = 3

# Seconds to wait between retries
RETRY_DELAY = 2.0

# Map currency symbols → ISO codes
CURRENCY_SYMBOLS: dict[str, str] = {
    "A$": "AUD",  # Must come before "$" so it matches first
    "C$": "CAD",
    "$":  "USD",
    "€":  "EUR",
    "£":  "GBP",
    "₺":  "TRY",
    "₹":  "INR",
    "¥":  "JPY",
    "₩":  "KRW",
}

# CSS class/id patterns that typically wrap the *current* sale/listing price.
# We deliberately avoid words like "original", "compare", "was"
# which usually mean a struck-through old price.
PRICE_CLASS_PATTERN = re.compile(
    r"(^|\b)(price|our[_\-]?price|sale[_\-]?price|current[_\-]?price"
    r"|product[_\-]?price|offer[_\-]?price|final[_\-]?price)(\b|$)",
    re.IGNORECASE,
)

# Class/id patterns that suggest OLD or struck-through prices — skip these
SKIP_CLASS_PATTERN = re.compile(
    r"(original|compare|was|old|strike|cross|line.?through|regular)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def detect_currency(raw: str) -> str:
    """
    Look for a currency symbol or ISO code in a raw price string.
    Returns the 3-letter ISO code (e.g. "USD") or "?" if nothing found.
    """
    # Check multi-char symbols first so "A$" is matched before "$"
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in raw:
            return code

    # Check for ISO codes at the end, e.g. "19.99 USD"
    match = re.search(
        r"\b(USD|EUR|GBP|TRY|INR|JPY|KRW|CHF|AUD|CAD)\b",
        raw,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    return "?"


def parse_price_number(text: str) -> float | None:
    """
    Convert a price-like string into a float.
    Handles both US format (1,299.99) and European format (1.299,99).
    Returns None if it can't be parsed, is <= 0, or looks unreasonable.
    """
    # Keep only digits, commas, and periods
    cleaned = re.sub(r"[^\d.,]", "", text).strip()

    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        # Whichever separator appears LAST is the decimal one
        if cleaned.rfind(",") > cleaned.rfind("."):
            # European: "1.299,99" → "1299.99"
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US: "1,299.99" → "1299.99"
            cleaned = cleaned.replace(",", "")

    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Decimal comma: "9,99" → "9.99"
            cleaned = cleaned.replace(",", ".")
        else:
            # Thousands separator: "1,299" → "1299"
            cleaned = cleaned.replace(",", "")

    try:
        value = float(cleaned)
        # Sanity check: reject prices outside a believable range
        if value <= 0 or value > 1_000_000:
            return None
        return value
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Four extraction strategies (tried in priority order)
# ---------------------------------------------------------------------------

def _try_json_ld(soup: BeautifulSoup) -> tuple[float, str] | None:
    """
    Strategy 1 — JSON-LD structured data (MOST RELIABLE).

    Many stores embed machine-readable product info in a <script> tag like:
      <script type="application/ld+json">
        { "@type": "Product", "offers": { "price": "19.99", "priceCurrency": "USD" } }
      </script>

    This is designed for machines to read, so it's the cleanest source.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # JSON-LD can be a single object or a list
        items = data if isinstance(data, list) else [data]

        for item in items:
            # Some sites use @graph (a list of things on the page)
            if "@graph" in item:
                items.extend(item["@graph"])
                continue

            if item.get("@type") in ("Product", "IndividualProduct"):
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                raw_price = str(offers.get("price", ""))
                currency = str(offers.get("priceCurrency", "?")).upper()
                price = parse_price_number(raw_price)
                if price:
                    logger.debug(f"[Scraper] JSON-LD hit: {price} {currency}")
                    return price, currency

    return None


def _try_meta_tags(soup: BeautifulSoup) -> tuple[float, str] | None:
    """
    Strategy 2 — HTML <meta> price tags.

    Open Graph and Twitter card standards expose price in <meta> tags:
      <meta property="product:price:amount" content="19.99">
      <meta property="product:price:currency" content="USD">
    """
    price_meta_names = [
        "product:price:amount",
        "og:price:amount",
        "twitter:data1",
        "price",
    ]
    currency_meta_names = [
        "product:price:currency",
        "og:price:currency",
        "twitter:data2",
        "currency",
    ]

    price = None
    currency = "?"

    for name in price_meta_names:
        # Meta tags can use either "property" or "name" attribute
        tag = (soup.find("meta", attrs={"property": name}) or
               soup.find("meta", attrs={"name": name}))
        if tag:
            raw = tag.get("content", "")
            price = parse_price_number(raw)
            if price:
                currency = detect_currency(raw)
                break

    if not price:
        return None

    # Try to also pick up the currency from its dedicated meta tag
    for name in currency_meta_names:
        tag = (soup.find("meta", attrs={"property": name}) or
               soup.find("meta", attrs={"name": name}))
        if tag:
            found = tag.get("content", "").strip().upper()
            if found:
                currency = found
            break

    logger.debug(f"[Scraper] Meta tag hit: {price} {currency}")
    return price, currency


def _try_css_selectors(soup: BeautifulSoup) -> tuple[float, str] | None:
    """
    Strategy 3 — CSS class / id / attribute selectors.

    Looks for HTML elements whose class or id contains "price" keywords,
    but skips elements that suggest a struck-through / old price.
    Also checks itemprop="price" (schema.org microdata).
    """
    # itemprop="price" is the schema.org microdata standard
    tag = soup.find(attrs={"itemprop": "price"})
    if tag:
        raw = tag.get("content") or tag.get_text(strip=True)
        price = parse_price_number(raw)
        if price:
            currency = detect_currency(raw)
            logger.debug(f"[Scraper] itemprop hit: {price} {currency}")
            return price, currency

    # data-price attribute (used by some stores)
    tag = soup.find(attrs={"data-price": True})
    if tag:
        raw = str(tag.get("data-price", ""))
        price = parse_price_number(raw)
        if price:
            logger.debug(f"[Scraper] data-price hit: {price}")
            return price, "?"

    # Walk all tags looking for class/id that matches our price pattern
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        combined = f"{classes} {tag_id}"

        if PRICE_CLASS_PATTERN.search(combined) and not SKIP_CLASS_PATTERN.search(combined):
            raw = tag.get_text(strip=True)
            # Skip containers with too much text (we want a single price, not a block)
            if len(raw) > 30:
                continue
            price = parse_price_number(raw)
            if price:
                currency = detect_currency(raw)
                logger.debug(f"[Scraper] CSS class hit: {price} {currency} — '{combined.strip()}'")
                return price, currency

    return None


def _try_text_scan(soup: BeautifulSoup) -> tuple[float, str] | None:
    """
    Strategy 4 — Full-page text scan (last resort).

    Removes script/style blocks and scans the remaining text for anything
    that looks like a price with a currency symbol next to it.
    This is imprecise and may grab wrong numbers; only runs if all else fails.
    """
    # Remove non-visible content so we don't grab JS variable numbers
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")

    # Match optional currency symbol + number + optional ISO code
    matches = re.findall(
        r"(A\$|C\$|[$€£₺₹¥₩])?\s?(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{2})?)"
        r"\s?(USD|EUR|GBP|TRY|INR|JPY|KRW|CHF|AUD|CAD)?",
        text,
    )

    for symbol, number, code in matches:
        # Only consider this a price if we found SOME currency indicator
        if not symbol and not code:
            continue
        price = parse_price_number(number)
        if price:
            raw_currency = symbol or code
            currency = detect_currency(raw_currency)
            logger.debug(f"[Scraper] Text scan hit: {price} {currency}")
            return price, currency

    return None


# ---------------------------------------------------------------------------
# Public API — called by handlers.py and price_checker.py
# ---------------------------------------------------------------------------

async def get_price(url: str) -> tuple[float, str] | tuple[None, None]:
    """
    Download the page at `url` and try to extract its price.

    Returns a tuple: (price_as_float, currency_iso_code)
    Examples:
      (19.99, "USD")
      (1299.0, "EUR")
      (None, None)    ← price could not be found

    Internally tries 4 strategies from most to least reliable:
      1. JSON-LD structured data
      2. <meta> tags
      3. CSS class/id selectors
      4. Full-page text scan
    """
    html = await _fetch_with_retry(url)
    if html is None:
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    strategies = [
        ("JSON-LD",       _try_json_ld),
        ("Meta tags",     _try_meta_tags),
        ("CSS selectors", _try_css_selectors),
        ("Text scan",     _try_text_scan),
    ]

    for name, strategy_fn in strategies:
        result = strategy_fn(soup)
        if result:
            price, currency = result
            logger.info(f"[Scraper] ✅ Found via {name}: {price} {currency} — {url}")
            return price, currency

    logger.warning(
        f"[Scraper] ❌ No price found for: {url}\n"
        "         Tip: This site may load prices via JavaScript (common on big stores).\n"
        "         Basic HTML scraping can't handle that without extra tools like Playwright."
    )
    return None, None


async def _fetch_with_retry(url: str) -> str | None:
    """
    Download a page and return its HTML as a string.
    Retries up to MAX_RETRIES times with RETRY_DELAY seconds between attempts.
    Returns None if all attempts fail.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                timeout=15,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.warning(f"[Scraper] HTTP {status} on attempt {attempt}/{MAX_RETRIES} — {url}")
            # 403 Forbidden or 429 Too Many Requests: stop immediately
            if status in (403, 429):
                logger.warning("[Scraper] Site is blocking us (403/429). Not retrying.")
                return None

        except httpx.TimeoutException:
            logger.warning(f"[Scraper] Timeout on attempt {attempt}/{MAX_RETRIES} — {url}")

        except Exception as e:
            logger.warning(f"[Scraper] Error on attempt {attempt}/{MAX_RETRIES} — {url}: {e}")

        # Wait a moment before the next attempt
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    logger.error(f"[Scraper] Gave up after {MAX_RETRIES} attempts — {url}")
    return None
