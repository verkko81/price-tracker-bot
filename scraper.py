"""
scraper.py — бесплатный парсер без ScrapingBee.
Работает через httpx + BeautifulSoup.
Подходит для обычных интернет-магазинов и Tori.
"""

import re
import json
import asyncio
import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_RETRIES = 3
RETRY_DELAY = 2.0


def detect_currency(text: str) -> str:
    if "€" in text:
        return "EUR"
    if "$" in text:
        return "USD"
    if "£" in text:
        return "GBP"

    match = re.search(r"\b(EUR|USD|GBP|SEK|NOK|DKK)\b", text, re.I)
    if match:
        return match.group(1).upper()

    return "?"


def parse_price_number(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.,]", "", text).strip()

    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        value = float(cleaned)
        if value <= 0 or value > 1_000_000:
            return None
        return value
    except ValueError:
        return None


def try_json_ld(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]

        for item in items:
            if not isinstance(item, dict):
                continue

            if "@graph" in item and isinstance(item["@graph"], list):
                items.extend(item["@graph"])

            if item.get("@type") in ("Product", "IndividualProduct"):
                offers = item.get("offers") or {}

                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                if isinstance(offers, dict):
                    raw_price = str(offers.get("price", ""))
                    currency = str(offers.get("priceCurrency", "?")).upper()

                    price = parse_price_number(raw_price)

                    if price:
                        return price, currency

    return None, None


def try_meta_tags(soup: BeautifulSoup):
    price_names = [
        "product:price:amount",
        "og:price:amount",
        "price",
        "twitter:data1",
    ]

    for name in price_names:
        tag = (
            soup.find("meta", attrs={"property": name})
            or soup.find("meta", attrs={"name": name})
        )

        if tag:
            raw = tag.get("content", "")
            price = parse_price_number(raw)

            if price:
                currency = detect_currency(raw)

                currency_tag = (
                    soup.find("meta", attrs={"property": "product:price:currency"})
                    or soup.find("meta", attrs={"name": "product:price:currency"})
                    or soup.find("meta", attrs={"property": "og:price:currency"})
                    or soup.find("meta", attrs={"name": "og:price:currency"})
                )

                if currency_tag and currency_tag.get("content"):
                    currency = currency_tag.get("content").upper()

                return price, currency

    return None, None


def try_price_elements(soup: BeautifulSoup):
    price_pattern = re.compile(r"(price|hinta|amount|current|sale)", re.I)
    skip_pattern = re.compile(r"(old|was|regular|strike|compare|original)", re.I)

    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        combined = f"{classes} {tag_id}"

        if price_pattern.search(combined) and not skip_pattern.search(combined):
            raw = tag.get_text(" ", strip=True)

            if len(raw) > 100:
                continue

            price = parse_price_number(raw)

            if price:
                currency = detect_currency(raw)
                return price, currency

    return None, None


def try_text_scan(soup: BeautifulSoup):
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)

    matches = re.findall(
        r"(€|\$|£)?\s?(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s?(EUR|USD|GBP)?",
        text,
        re.I,
    )

    for symbol, number, code in matches:
        if not symbol and not code:
            continue

        price = parse_price_number(number)

        if price:
            currency = detect_currency(symbol or code)
            return price, currency

    return None, None


async def fetch_html(url: str) -> str | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text

        except Exception as e:
            logger.warning(f"[Scraper] Attempt {attempt}/{MAX_RETRIES} failed: {e}")

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    return None


async def get_price(url: str) -> tuple[float, str] | tuple[None, None]:
    html = await fetch_html(url)

    if not html:
        logger.warning(f"[Scraper] Could not fetch page: {url}")
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    strategies = [
        try_json_ld,
        try_meta_tags,
        try_price_elements,
        try_text_scan,
    ]

    for strategy in strategies:
        price, currency = strategy(soup)

        if price:
            logger.info(f"[Scraper] Found price: {price} {currency}")
            return price, currency

    logger.warning(f"[Scraper] No price found: {url}")
    return None, None