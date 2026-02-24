"""
ai_summarize.py - Generate a YouTube Short script using GPT-4o-mini.
Produces a punchy voiceover script optimized for 30-60 second Shorts.

Cost: ~$0.01-0.03 per video (gpt-4o-mini)
"""
import logging
from openai import OpenAI
import config

logger = logging.getLogger(__name__)
client = OpenAI(api_key=config.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are a viral YouTube Shorts scriptwriter specializing in Amazon product reviews.
Your scripts are energetic, punchy, and designed to hook viewers in the first 3 seconds.
You always sound like an enthusiastic friend sharing a great find — never like an advertisement.
Keep scripts between 120-160 words so TTS stays under 60 seconds.
Always end with a call to action."""

SCRIPT_TEMPLATE = """Write a YouTube Shorts voiceover script for this Amazon product review video.

PRODUCT: {title}
PRICE: {price}
RATING: {rating}/5 stars ({review_count} reviews)
CATEGORY: {category}
AFFILIATE LINK: {affiliate_url}

TOP CUSTOMER REVIEWS:
{reviews_text}

REQUIREMENTS:
- Hook in first sentence (grab attention immediately)
- Mention the product name naturally
- Summarize what customers LOVE about it
- Mention 1-2 drawbacks honestly (builds trust)  
- Mention the price casually
- End with: "Link in bio and description!"
- 120-160 words MAX
- Conversational, energetic tone
- NO hashtags in the script (add those separately)
- Write ONLY the voiceover text, no stage directions

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
        script = response.choices[0].message.content.strip()
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
2. A video description (2-3 sentences + affiliate link + hashtags)
3. 10 relevant hashtags (comma separated, include #Shorts #AmazonFinds)

Product price: {product.get('price', '')}
Affiliate URL: {product.get('affiliate_url', '')}

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

        # Append affiliate URL to description
        affiliate_url = product.get("affiliate_url", "")
        if affiliate_url and affiliate_url not in description:
            description += f"\n\n🛒 Shop here: {affiliate_url}"

        return {
            "title": title or f"🛒 {product['title'][:55]}",
            "description": description,
            "hashtags": hashtags,
        }

    except Exception as e:
        logger.warning(f"Metadata generation failed: {e}")
        tags = ["#Shorts", "#AmazonFinds", "#ProductReview", f"#{product.get('category', 'Amazon')}"]
        return {
            "title": f"🛒 {product['title'][:55]}",
            "description": f"Check out this amazing find!\n\n🛒 {product.get('affiliate_url', '')}",
            "hashtags": tags,
        }
