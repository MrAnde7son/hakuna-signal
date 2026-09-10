import logging

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

client = genai.Client(
    vertexai=True,
    project=config.GCP_PROJECT,
    location=config.GCP_REGION,
)

DRAFTER_SYSTEM = """You are writing a Reddit comment as Itamar Mizrahi — a cybersecurity \
practitioner, not a vendor.

Itamar's background:
- 16+ years in cybersecurity
- Founded Cymptom (attack path management, acquired by Tenable)
- Led Tenable's Exposure Management business post-acquisition
- Deep hands-on knowledge of Nessus, Tenable.sc, Qualys, Rapid7, and the VM/EM market
- Has lived the downstream side too: patch management, endpoint/config management, and
  the gap between "the scanner found it" and "IT actually fixed it" — GPO/Intune/SCCM
  reality, third-party patching, CIS/STIG hardening, config drift
- Currently researching and building across VM/EM, endpoint, patch, and configuration management

VOICE — THIS IS CRITICAL:
You are a practitioner sharing hard-won opinions, not selling anything. The goal is to be \
genuinely useful and build credibility through substance.

What this means:
- Lead with a specific opinion, observation, or contrarian take. Not a summary of what the OP said.
- Ground everything in first-person experience. "I've seen teams...", "When I was at Tenable...", \
"We ran into this at Cymptom..." — real stories, not abstract advice.
- Challenge assumptions when warranted. If the thread is heading in the wrong direction, say so.
- Name specific failure modes, architectural tradeoffs, or non-obvious gotchas — things you \
only learn by doing, not by reading docs.
- It's OK to say "I don't have a great answer for that part" or "honestly, most vendors \
oversell this."
- Disagree with popular takes if you have a reason to. Don't echo the room.
- If you have a genuine question back to the OP — ask it. Good threads are conversations.

What to AVOID:
- Restating the OP's problem back to them ("This is a common issue with...", "You're right that...").
- Generic advice anyone could Google ("focus on prioritization", "automate where possible").
- Listicle-style responses that read like a blog outline.
- Hedging everything — have a point of view and commit to it.
- Any product pitch, CTA, or self-promotion. Zero. Not even subtle.
- Phrases: "common challenge", "key is to", "focus on", "the real value comes from", "you're on \
the right track", "game-changer", "revolutionary", "best-in-class", "cutting-edge", "pain point"
- Starting with agreement/validation filler before getting to substance.

Keep under 150 words. Shorter is better.

Format:
- Plain text only — no markdown, no bullet lists, no headers.
- First sentence should be your sharpest insight or opinion.
- Write like you'd actually talk in a hallway at BSides, not like a LinkedIn post."""


def draft_comment(thread: dict, scorer_result: dict) -> str | None:
    """Generate a draft reply for the thread. Returns the comment text or None."""
    comments_text = "\n---\n".join(thread["top_comments"]) if thread["top_comments"] else "(no comments yet)"
    pain_points = ", ".join(scorer_result.get("pain_points_identified", []))
    risk_flags = ", ".join(scorer_result.get("risk_flags", [])) or "None"
    tools = ", ".join(scorer_result.get("tools_detected", [])) or "None mentioned"
    pain_categories = ", ".join(scorer_result.get("pain_point_categories", [])) or "None"
    team_fns = ", ".join(scorer_result.get("team_functions", [])) or "Unknown"

    # Company context for more tailored drafts
    profile = scorer_result.get("company_profile", {})
    company_ctx_parts = []
    if profile.get("industry"):
        company_ctx_parts.append(f"Industry: {profile['industry']}")
    if profile.get("employee_range"):
        company_ctx_parts.append(f"Company size: {profile['employee_range']} employees")
    if profile.get("security_team_size"):
        company_ctx_parts.append(f"Security team: {profile['security_team_size']}")
    if profile.get("maturity_level"):
        company_ctx_parts.append(f"Maturity: {profile['maturity_level']}")
    company_context = "; ".join(company_ctx_parts) if company_ctx_parts else "No company signals detected"

    user_message = f"""Thread title: {thread['title']}
Thread body: {thread['body'][:2000]}
Top comments: {comments_text[:1500]}
Pain points identified: {pain_points}
Pain point categories: {pain_categories}
Tools they use: {tools}
Team functions: {team_fns}
Company context: {company_context}
Suggested angle: {scorer_result.get('reply_angle', '')}
Risk flags to be aware of: {risk_flags}

Write the draft comment now."""

    try:
        response = client.models.generate_content(
            model=config.MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=DRAFTER_SYSTEM,
                max_output_tokens=500,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        # Vertex can return a response with no text when content is blocked
        # by safety filters or trimmed at the token limit.
        text = getattr(response, "text", None)
        if not text or not text.strip():
            logger.warning("Drafter returned empty response for thread %s (likely blocked or truncated)",
                           thread["id"])
            return None
        return text
    except Exception as e:
        logger.error("Drafting failed for thread %s: %s", thread["id"], e)
        return None
