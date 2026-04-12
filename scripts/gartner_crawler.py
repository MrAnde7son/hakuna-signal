import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright
from tqdm import tqdm


MARKETS = [
    "exposure-assessment-platforms",
    "vulnerability-assessment",
    "cyber-asset-attack-surface-management",
    "external-attack-surface-management",
]

BASE = "https://www.gartner.com/reviews/market/{}"

OUTPUT_DIR = Path("gartner_dump")

MAX_REVIEW_PAGES = 10


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_html(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


async def wait_for_content(page, timeout=30000):
    """Wait until Cloudflare challenge resolves and real content appears."""
    try:
        await page.wait_for_function(
            "document.title && !document.title.includes('Just a moment')",
            timeout=timeout,
        )
    except Exception:
        pass
    await page.wait_for_timeout(3000)


def parse_vendors_from_html(html):
    """Parse vendor product cards from a market page HTML using BeautifulSoup."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    vendors = []
    seen = set()

    # Product links use: /reviews/product/<slug>?marketSeoName=<market>
    for a in soup.select('a[href*="/reviews/product/"]'):
        href = a.get("href", "")
        if "compare" in href:
            continue

        text = a.get_text(strip=True)
        if not text or len(text) < 2:
            continue

        # Extract slug from href
        path = href.split("?")[0]
        slug = path.rstrip("/").split("/")[-1]

        if slug in seen:
            continue
        seen.add(slug)

        # Normalize URL
        if href.startswith("/"):
            url = "https://www.gartner.com" + href
        else:
            url = href

        # Strip query params for the canonical product URL
        base_url = url.split("?")[0]

        vendors.append({
            "product": text,
            "slug": slug,
            "url": base_url,
        })

    # Try to extract ratings from the market page card context
    for vendor in vendors:
        # Find the product card containing this vendor
        for card in soup.select('[class*="productCard"]'):
            title_el = card.select_one('[class*="productTitle"]')
            if title_el and title_el.get_text(strip=True) == vendor["product"]:
                # Rating
                rating_el = card.select_one('[class*="rating"]')
                if rating_el:
                    m = re.search(r"(\d+\.\d+)", rating_el.get_text())
                    if m:
                        vendor["rating"] = float(m.group(1))

                # Rating count
                count_el = card.select_one('[class*="ratingsCount"]')
                if count_el:
                    m = re.search(r"(\d+)", count_el.get_text())
                    if m:
                        vendor["review_count"] = int(m.group(1))

                # Vendor/company name
                vendor_el = card.select_one('[class*="vendor"]')
                if vendor_el:
                    v_text = vendor_el.get_text(strip=True)
                    vendor["vendor"] = v_text[2:] if v_text.startswith("By") else v_text

                # Description
                desc_el = card.select_one('[class*="truncatedText"]')
                if desc_el:
                    vendor["description"] = desc_el.get_text(strip=True)

                break

    return vendors


def parse_reviews_from_html(html):
    """Parse individual reviews from a product reviews page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    reviews = []

    # Reviews are typically in cards/containers with review-related classes
    review_containers = soup.select(
        '[class*="reviewCard"], [class*="review-card"], '
        '[class*="ReviewCard"], [class*="reviewListing"]'
    )

    # Fallback: look for article elements or divs that look like reviews
    if not review_containers:
        review_containers = soup.select("article, [role='article']")

    # Second fallback: find containers that have both a rating and text content
    if not review_containers:
        for el in soup.select("div"):
            classes = " ".join(el.get("class", []))
            if "review" in classes.lower():
                review_containers.append(el)

    for el in review_containers:
        text = el.get_text()
        if len(text) < 50:
            continue

        review = {}

        # Title
        for sel in ["h2", "h3", "h4", '[class*="title"]', '[class*="Title"]']:
            title_el = el.select_one(sel)
            if title_el:
                review["title"] = title_el.get_text(strip=True)
                break

        # Rating
        for sel in ['[class*="rating"]', '[class*="Rating"]', '[class*="star"]',
                     '[aria-label*="star"]', '[aria-label*="rating"]']:
            rating_el = el.select_one(sel)
            if rating_el:
                aria = rating_el.get("aria-label", "")
                m = re.search(r"(\d+\.?\d*)", aria or rating_el.get_text())
                if m:
                    review["rating"] = float(m.group(1))
                    break

        # Body text
        for sel in ['[class*="body"]', '[class*="Body"]', '[class*="content"]',
                     '[class*="Content"]', '[class*="comment"]', "p"]:
            body_el = el.select_one(sel)
            if body_el and len(body_el.get_text(strip=True)) > 20:
                review["body"] = body_el.get_text(strip=True)
                break

        # Reviewer
        for sel in ['[class*="reviewer"]', '[class*="Reviewer"]',
                     '[class*="author"]', '[class*="Author"]']:
            rev_el = el.select_one(sel)
            if rev_el:
                review["reviewer"] = rev_el.get_text(strip=True)
                break

        # Date
        for sel in ['[class*="date"]', '[class*="Date"]', "time"]:
            date_el = el.select_one(sel)
            if date_el:
                review["date"] = (date_el.get("datetime") or date_el.get_text()).strip()
                break

        if review.get("title") or review.get("body"):
            reviews.append(review)

    return reviews


async def crawl_reviews(page, product_url, vendor_dir):
    """Crawl paginated reviews for a product."""
    all_reviews = []

    for page_num in range(1, MAX_REVIEW_PAGES + 1):
        url = product_url.rstrip("/") + f"?pg={page_num}"
        await page.goto(url)
        await wait_for_content(page)

        html = await page.content()
        save_html(vendor_dir / f"reviews_page_{page_num}.html", html)

        reviews = parse_reviews_from_html(html)
        if not reviews:
            break

        all_reviews.extend(reviews)
        print(f"    Page {page_num}: {len(reviews)} reviews")

    return all_reviews


async def crawl_product(context, product, market):
    """Crawl a single product's page and reviews."""
    url = product["url"]
    slug = product["slug"]
    vendor_dir = OUTPUT_DIR / "markets" / market / "vendors" / slug

    page = await context.new_page()
    try:
        await page.goto(url)
        await wait_for_content(page)

        html = await page.content()
        save_html(vendor_dir / "product.html", html)

        reviews = await crawl_reviews(page, url, vendor_dir)

        data = {
            **product,
            "market": market,
            "reviews": reviews,
            "total_reviews_crawled": len(reviews),
        }

        save_json(vendor_dir / "product.json", data)
        return data

    finally:
        await page.close()


async def crawl_market(context, market):
    """Crawl a Gartner market page and all its vendors."""
    url = BASE.format(market)
    market_dir = OUTPUT_DIR / "markets" / market

    page = await context.new_page()
    await page.goto(url)
    await wait_for_content(page)

    html = await page.content()
    save_html(market_dir / "market.html", html)
    await page.close()

    vendors = parse_vendors_from_html(html)
    save_json(market_dir / "vendors.json", vendors)
    print(f"  Found {len(vendors)} vendors in {market}")

    results = []
    for v in tqdm(vendors, desc=f"  {market}"):
        if not v.get("url"):
            continue
        data = await crawl_product(context, v, market)
        results.append(data)

    # Save combined market summary
    save_json(market_dir / "summary.json", {
        "market": market,
        "url": url,
        "vendor_count": len(vendors),
        "vendors": [
            {k: v for k, v in r.items() if k != "reviews"}
            for r in results
        ],
    })

    return results


async def main(markets=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )

        # Remove webdriver flag that Cloudflare detects
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        # Warm up the session by visiting the base site first
        # so Cloudflare challenge resolves before real crawling
        warmup = await context.new_page()
        await warmup.goto("https://www.gartner.com/reviews/home")
        await wait_for_content(warmup)
        await warmup.close()

        for market in (markets or MARKETS):
            print(f"Crawling market: {market}")
            await crawl_market(context, market)

        await browser.close()

    print("Done. Output in:", OUTPUT_DIR)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("markets", nargs="*", default=MARKETS,
                        help="Market slugs to crawl (default: all)")
    args = parser.parse_args()
    asyncio.run(main(args.markets))
