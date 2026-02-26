"""
ai_summarize.py - Generate a YouTube Short script using GPT-4o-mini.
Produces a punchy voiceover script optimized for 30-60 second Shorts.

Cost: ~$0.01-0.03 per video (gpt-4o-mini)
"""
import logging
import re
from openai import OpenAI
import config
from scripts.url_shortener import shorten

logger = logging.getLogger(__name__)
client = OpenAI(api_key=config.OPENAI_API_KEY)


def _clean_script(text: str) -> str:
    """
    Strip anything that shouldn't be spoken aloud:
    - Markdown links: [text](url) → text
    - Raw URLs (http/https)
    - Stage directions in brackets/parens
    """
    # Convert markdown links to just the display text
    text = re.sub(r'\[([^\]]+)\]\(https?://[^\)]+\)', r'\1', text)
    # Remove bare URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove stage directions like (pause) or [music]
    text = re.sub(r'\([^)]{1,40}\)', '', text)
    text = re.sub(r'\[[^\]]{1,40}\]', '', text)
    # Clean up extra spaces/newlines
    text = re.sub(r'  +', ' ', text).strip()
    return text

SYSTEM_PROMPT = """You are a viral YouTube Shorts scriptwriter specializing in Amazon product reviews.
Your scripts are energetic, punchy, and designed to hook viewers in the first 3 seconds.
You always sound like an enthusiastic friend sharing a great find — never like an advertisement.
Keep scripts between 120-160 words so TTS stays under 60 seconds.
CRITICAL RULES:
- NEVER include URLs, links, or web addresses in the script — not even shortened ones
- NEVER use markdown formatting like [text](url) — plain text only
- Always end with exactly: "Check the link in the description to grab yours!"
- Write ONLY what will be spoken aloud"""

SCRIPT_TEMPLATE = """Write a YouTube Shorts voiceover script for this Amazon product review video.

PRODUCT: {title}
PRICE: {price}
RATING: {rating}/5 stars ({review_count} reviews)
CATEGORY: {category}

TOP CUSTOMER REVIEWS:
{reviews_text}

REQUIREMENTS:
- Hook in first sentence (grab attention immediately)
- Mention the product name naturally
- Summarize what customers LOVE about it
- Mention 1-2 drawbacks honestly (builds trust)
- Mention the price casually
- End with EXACTLY: "Check the link in the description to grab yours!"
- 120-160 words MAX
- Conversational, energetic tone
- NO hashtags, NO URLs, NO markdown, NO stage directions
- Plain spoken text only

Script:"""


def generate_script(product: dict, reviews: list[dict], reviews_text: str) -> dict:
    """
    Generate a short video script for a product.
    Returns dict with: script, title, description, tags
    """
    prompt = SCRIPT_TEMPLATE.format(
        title=product.get("title", ""),
        price=product.get("price", ""),
        rating=product.get("rating", "N/A"),
        review_count=f"{product.get('review_count', 0):,}" if product.get("review_count") else "thousands of",
        category=product.get("category", ""),
        affiliate_url=product.get("affiliate_url", ""),
        reviews_text=reviews_text or "Great product with overwhelmingly positive reviews.",
    )

    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.8,
        )
        script = _clean_script(response.choices[0].message.content.strip())
        logger.info(f"Generated script ({len(script.split())} words)")

        # Generate metadata
        metadata = _generate_metadata(product, script)
        return {
            "script": script,
            **metadata,
        }

    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        raise


def _generate_metadata(product: dict, script: str) -> dict:
    """Generate YouTube/Instagram title, description, and hashtags."""
    title_prompt = f"""For this YouTube Shorts video about "{product['title']}", generate:
1. A catchy video title (max 60 chars, include an emoji)
2. A video description (2-3 engaging sentences about the product — no URLs, no hashtags, just the hook)
3. 10 relevant hashtags (comma separated, include #Shorts #AmazonFinds)

Product price: {product.get('price', '')}

Format your response as:
TITLE: [title here]
DESCRIPTION: [description here]
HASHTAGS: [hashtags here]"""

    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "user", "content": title_prompt}],
            max_tokens=300,
            temperature=0.7,
        )
        text = response.choices[0].message.content.strip()

        title = ""
        description = ""
        hashtags = []

        for line in text.split("\n"):
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip()
            elif line.startswith("DESCRIPTION:"):
                description = line.replace("DESCRIPTION:", "").strip()
            elif line.startswith("HASHTAGS:"):
                raw_tags = line.replace("HASHTAGS:", "").strip()
                hashtags = [t.strip() for t in raw_tags.split(",")]

        # Ensure #Shorts is always present for YouTube algorithm
        if "#Shorts" not in hashtags:
            hashtags.insert(0, "#Shorts")

        # Build clean description: hook text + Bitly link + hashtags
        affiliate_url = product.get("affiliate_url", "")
        short_url = shorten(affiliate_url) if affiliate_url else affiliate_url
        hashtag_str = " ".join(hashtags)
        full_description = f"{description}\n\n🛒 {short_url}\n\n{hashtag_str}"

        return {
            "title": title or f"🛒 {product['title'][:55]}",
            "description": full_description,
            "hashtags": hashtags,
            "short_url": short_url,
        }

    except Exception as e:
        logger.warning(f"Metadata generation failed: {e}")
        affiliate_url = product.get("affiliate_url", "")
        short_url = shorten(affiliate_url) if affiliate_url else ""
        tags = ["#Shorts", "#AmazonFinds", "#ProductReview", f"#{product.get('category', 'Amazon')}"]
        hashtag_str = " ".join(tags)
        return {
            "title": f"🛒 {product['title'][:55]}",
            "description": f"Check out this amazing find!\n\n🛒 {short_url or affiliate_url}\n\n{hashtag_str}",
            "hashtags": tags,
            "short_url": short_url,
        }
