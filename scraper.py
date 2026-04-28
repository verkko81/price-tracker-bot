"""
scraper.py — Downloads a product page and extracts the price + currency.
Uses normal request first. If price is not found, tries ScrapingBee.
"""

import os
import re
import json
import asyncio
import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_RETRIES = 3
RETRY_DELAY = 2.0

CURRENCY_SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "A$": "AUD",
    "C$": "CAD",
    "₺": "TRY",
    "₹": "INR",
    "¥": "JPY",
    "₩": "KRW",
}


def detect_currency(raw: str) -> str:
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in raw:
            return code

    match = re.search(r"\b(USD|EUR|GBP|CHF|AUD|CAD|JPY|KRW|TRY|INR)\b", raw, re.I)
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


def extract_price_from_html(html: str, url: str) -> tuple[float, str] | tuple[None, None]:
    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD
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
                        logger.info(f"[Scraper] Found JSON-LD price: {price} {currency}")
                        return price, currency

    # 2. Meta tags
    meta_price_names = [
        "product:price:amount",
        "og:price:amount",
        "price",
    ]

    for name in meta_price_names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
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

                logger.info(f"[Scraper] Found meta price: {price} {currency}")
                return price, currency

    # 3. Common price classes
    price_pattern = re.compile(r"(price|hinta|current|sale)", re.I)
    skip_pattern = re.compile(r"(old|was|regular|strike|compare|original)", re.I)

    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        combined = f"{classes} {tag_id}"

        if price_pattern.search(combined) and not skip_pattern.search(combined):
            raw = tag.get_text(" ", strip=True)

            if len(raw) > 80:
                continue

            price = parse_price_number(raw)
            if price:
                currency = detect_currency(raw)
                logger.info(f"[Scraper] Found CSS price: {price} {currency}")
                return price, currency

    # 4. Text scan
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")

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
            logger.info(f"[Scraper] Found text price: {price} {currency}")
            return price, currency

    logger.warning(f"[Scraper] No price found for {url}")
    return None, None


async def fetch_normal(url: str) -> str | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text

        except Exception as e:
            logger.warning(f"[Scraper] Normal fetch failed {attempt}/{MAX_RETRIES}: {e}")

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    return None


async def fetch_scrapingbee(url: str) -> str | None:
    if not SCRAPINGBEE_API_KEY:
        logger.warning("[Scraper] SCRAPINGBEE_API_KEY is missing")
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                "https://app.scrapingbee.com/api/v1/",
                params={
                    "api_key": SCRAPINGBEE_API_KEY,
                    "url": url,
                    "render_js": "true",
                    "premium_proxy": "true",
                    "country_code": "fi",
                    "wait": "3000",
                },
            )
            response.raise_for_status()
            return response.text

    except Exception as e:
        logger.error(f"[Scraper] ScrapingBee failed: {e}")
        return None


async def get_price(url: str) -> tuple[float, str] | tuple[None, None]:
    html = await fetch_normal(url)

    if html:
        price, currency = extract_price_from_html(html, url)
        if price:
            return price, currency

    logger.info("[Scraper] Trying ScrapingBee...")

    html = await fetch_scrapingbee(url)

    if html:
        return extract_price_from_html(html, url)

    return None, None