import json
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

client = genai.Client(
    vertexai=True,
    project=config.GCP_PROJECT,
    location=config.GCP_REGION,
)

SCORER_SYSTEM = """You are an objective market research analyst studying how cybersecurity \
practitioners talk about vulnerability management (VM) and exposure management (EM). \
You are monitoring Reddit to understand real practitioner needs, frustrations, workflows, \
and tool opinions — across all company sizes and maturity levels.

Your goal is to extract honest, unbiased intelligence about the VM/EM landscape. You are NOT \
scoring for any specific product or company. You are building a picture of what practitioners \
actually care about.

RELEVANCE SCORE — how relevant is this thread to understanding VM/EM practitioner needs:
- 10: Deep, first-hand account of a VM/EM operational struggle with specific details
- 8–9: Clear discussion of VM/EM pain points, tool opinions, or workflow challenges
- 6–7: Adjacent security topic with VM/EM implications (e.g., asset management, compliance)
- 4–5: General security discussion, loosely related to VM/EM
- 1–3: Not related to VM/EM practice

Extract the following intelligence from the thread:

PAIN POINTS — describe exactly what the poster (and commenters) are struggling with. Use their \
language, not marketing speak. Capture the real frustration, not a sanitized category label. \
If someone says "Nessus keeps flagging stuff we patched 3 weeks ago" — that's the pain point, \
not "scan accuracy issues."

REPLY ANGLE — if a senior VM/EM practitioner wanted to add value to this thread, what specific \
experience or insight would actually help? This should be about helping the person, not about \
promoting anything.

RISK FLAGS — anything that makes engaging risky (e.g., poster is venting not asking for help, \
thread is hostile to vendors, likely a student/homework question, etc.)

COMPANY PROFILE — infer from context clues (org size mentions, tool stack, team descriptions):
- company_name: explicit name if mentioned, otherwise null
- industry: vertical if detectable, otherwise null
- employee_range: "1-100", "100-500", "500-2000", "2000+", or null
- security_team_size: "solo", "2-5", "5-10", "10+", or null
- maturity_level: "low" (no formal VM program), "medium" (has tools, struggling with process), \
"high" (mature, optimizing), or null

TOOLS DETECTED — every security/IT tool explicitly named in the thread or comments. \
Only include tools actually mentioned, not inferred.

PAIN POINT CATEGORIES — classify into one or more:
- prioritization: Can't determine what to fix first
- cost: Tool pricing, budget constraints, licensing friction
- integration: Tools don't connect to each other or to ticketing/workflow
- coverage: Asset discovery gaps, shadow IT, incomplete inventory
- workflow: Manual processes, spreadsheet tracking, no automation
- alert_fatigue: Too many findings, no context, signal-to-noise problems
- staffing: Understaffed, wearing multiple hats, no dedicated VM role
- reporting: Can't communicate risk meaningfully to leadership or auditors
- compliance: Regulatory/audit pressure as a driver
- tool_sprawl: Too many disconnected tools, no unified view
- accuracy: False positives, stale findings, scanner not reflecting reality
- remediation: Difficulty getting IT/dev teams to actually fix things
- asset_management: Don't know what they have, CMDB is stale or incomplete

TEAM FUNCTIONS — what security functions does this person/team handle:
- vulnerability_management, soc, incident_response, it_ops, compliance, grc, \
appsec, cloud_security, network_security, endpoint_security, penetration_testing

Respond ONLY with valid JSON (no other text):
{"score": integer, "pain_points_identified": [strings — use the poster's own words/framing], \
"reply_angle": "string", "risk_flags": [strings], \
"recommended_action": "reply"|"monitor"|"discard", \
"company_profile": {"company_name": string_or_null, "industry": string_or_null, \
"employee_range": string_or_null, "security_team_size": string_or_null, \
"maturity_level": string_or_null}, "tools_detected": [strings], \
"pain_point_categories": [strings], "team_functions": [strings]}"""


def score_thread(thread: dict) -> dict | None:
    """Score a thread for relevance. Returns parsed JSON or None on failure."""
    created_dt = datetime.fromtimestamp(thread["created_utc"], tz=timezone.utc)
    time_ago = _time_ago(created_dt)

    comments_text = "\n---\n".join(thread["top_comments"]) if thread["top_comments"] else "(no comments yet)"

    source = thread.get("source", "reddit")
    category = thread.get("category", "")
    if source == "reddit":
        source_line = f"Source: Reddit (r/{category})"
        engagement_line = f"Thread upvotes: {thread['score']}"
    else:
        source_line = f"Source: {source} ({category})"
        engagement_line = f"Replies: {thread['score']}"

    user_message = f"""{source_line}
Title: {thread['title']}
Body: {thread['body'][:2000]}
Top comments (first 3): {comments_text[:1500]}
{engagement_line}
Posted: {time_ago}"""

    # temperature=0.3 + thinking_budget=0 — this is a structured classification
    # task, not a reasoning task. Low temp keeps the JSON shape stable; disabling
    # thinking is what makes Flash cheap enough to run every 30 minutes.
    response = None
    try:
        response = client.models.generate_content(
            model=config.MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=SCORER_SYSTEM,
                max_output_tokens=2000,
                temperature=0.3,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        raw = response.text[:200] if response is not None and getattr(response, "text", None) else "<empty>"
        logger.error("Scoring JSON parse failed for thread %s: %s (raw: %r)", thread["id"], e, raw)
        return None
    except Exception as e:
        logger.error("Scoring API call failed for thread %s: %s", thread["id"], e)
        return None


def _time_ago(dt: datetime) -> str:
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)} minutes ago"
    if hours < 24:
        return f"{int(hours)} hours ago"
    return f"{int(hours / 24)} days ago"
