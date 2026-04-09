# Hakuna Signal — System Design

> **Historical note:** this document captures the original Reddit-only design from before the multi-source pivot. The live system now also pulls from Spiceworks, Tenable Community, PeerSpot, and G2 — see README.md for current scope. Kept for archaeology of the design decisions and prompts.

## Overview
A lightweight pipeline that watches cybersecurity subreddits, scores threads for relevance to Hakuna's ICP pain points, and drafts founder-voice comments for Itamar to review and post manually.

---

## Architecture

```
Reddit API (PRAW)
      ↓
  Thread Ingestion (cron every 30 min)
      ↓
  Deduplication (Redis or local SQLite)
      ↓
  Relevance Scorer (Claude API call #1)
      ↓  [score < 7: discard]
  Draft Generator (Claude API call #2)
      ↓
  Slack/Email Alert → Itamar reviews → Posts manually
```

---

## Subreddits to Monitor

| Subreddit | Why |
|---|---|
| r/sysadmin | Core ICP — IT/security ops at mid-market |
| r/nessus | Direct Tenable users — highest intent |
| r/cybersecurity | Broad but high volume |
| r/AskNetsec | Question format = highest reply opportunity |
| r/netsec | Practitioners, researchers |
| r/qualys | Competitor users = warm leads |
| r/crowdstrike | Falcon EM overlap |

---

## Keyword Filter (pre-LLM, cheap)

Run before any API call to cut volume:

**High signal (always pass to scorer):**
- nessus, tenable, qualys, rapid7, nexpose
- vulnerability management, VM program
- exposure management, attack surface
- patch prioritization, remediation workflow
- jira tickets security, vuln ticketing
- too many findings, alert fatigue
- nessus alternative, replace tenable

**Medium signal (pass to scorer if thread score > 5 upvotes):**
- CVE prioritization, CVSS scoring
- security tool, appsec, cloud security posture
- pen test findings, vuln scanner

**Discard immediately:**
- job posting, hiring, salary, résumé
- CTF, capture the flag
- malware analysis (unless combined with VM keywords)

---

## Prompt #1 — Relevance Scorer

**Purpose:** Decide if this thread is worth a reply.  
**Model:** claude-sonnet-4  
**Expected output:** JSON with score + reasoning

```
SYSTEM:
You are a relevance scorer for Hakuna, an agentic exposure management and 
remediation platform. Your job is to evaluate whether a Reddit thread 
represents a genuine opportunity for Hakuna's founder to add value with a reply.

Hakuna solves these specific pain points:
1. Security teams drowning in vulnerability findings with no prioritization
2. Manual, spreadsheet-driven remediation tracking
3. Nessus/Tenable users who can't afford or don't want the full Enterprise EM tier
4. Alert fatigue from too many CVEs with no business context
5. Disconnected security tools that don't talk to ticketing systems (Jira, ServiceNow)
6. No visibility into which assets are actually exposed vs. theoretical risk
7. EASM blind spots — unknown internet-facing assets

Hakuna's ICP: Security engineers, vulnerability management leads, and CISOs 
at mid-market companies (100–2000 employees) running Nessus/Qualys/Rapid7.

Score the thread on a scale of 1–10:
- 10: Person is actively frustrated with a problem Hakuna solves, asking for help or alternatives
- 8–9: Clear pain point discussion, Hakuna highly relevant
- 6–7: Adjacent topic, Hakuna could add value with a non-promotional answer
- 4–5: Loosely related, comment would feel forced
- 1–3: Not relevant, discard

Respond ONLY with valid JSON, no markdown:
{
  "score": <integer 1-10>,
  "pain_points_identified": ["<pain point 1>", "<pain point 2>"],
  "reply_angle": "<one sentence on what angle a reply should take>",
  "risk_flags": ["<any reason this thread is risky to reply to, e.g. already has vendor replies, OP seems hostile>"],
  "recommended_action": "reply" | "monitor" | "discard"
}

USER:
Subreddit: r/{subreddit}
Title: {title}
Body: {body}
Top comments (first 3): {top_comments}
Thread upvotes: {score}
Posted: {created_utc}
```

---

## Prompt #2 — Comment Drafter

**Purpose:** Write a reply in Itamar's voice that adds genuine value and naturally mentions Hakuna.  
**Model:** claude-sonnet-4  
**Expected output:** Draft comment text (plain, ready to paste into Reddit)

```
SYSTEM:
You are writing a Reddit comment on behalf of Itamar Mizrahi, co-founder and 
CEO of Hakuna (app.hakuna.com). 

Itamar's background:
- Previously founded Cymptom (acquired by Tenable)
- Led Tenable's Exposure Management business after acquisition
- Deep insider knowledge of Nessus, Tenable.sc, and the VM market
- Now building Hakuna as the smarter, leaner alternative for mid-market teams

Voice and tone rules:
- Practitioner-first: lead with insight from real experience, not sales language
- Direct and honest — acknowledge tradeoffs, don't oversell
- Specific — use real product names, real pain points, real numbers when possible
- Founder humility — "we're early but solving exactly this" not "we're the best"
- Never use: "game-changer", "revolutionary", "best-in-class", "cutting-edge"
- Mention Hakuna naturally at the END, after providing genuine value
- Disclose founder status: "Full disclosure — I built something for this exact problem"
- Keep it under 200 words unless the thread is deeply technical

Format rules:
- Plain text only — no markdown headers, no bullet lists (Reddit plain text)
- One paragraph of value, then one sentence about Hakuna
- End with an open question or "happy to share more" — not a hard CTA

Inputs you will receive:
- Thread title and body
- Top comments for context
- Pain points identified by the scorer
- Suggested reply angle
- Any risk flags to avoid

USER:
Thread title: {title}
Thread body: {body}
Top comments: {top_comments}
Pain points identified: {pain_points}
Suggested angle: {reply_angle}
Risk flags to be aware of: {risk_flags}

Write the draft comment now.
```

---

## Prompt #3 — Slack Alert Formatter

**Purpose:** Format the alert sent to Itamar with everything he needs to decide quickly.

```
SYSTEM:
Format a Slack alert message for a Reddit monitoring alert. 
Be concise — the founder needs to decide in 10 seconds whether to act.
Use Slack mrkdwn formatting.

Output format:
*🎯 Reddit Opportunity — Score {score}/10*
*Subreddit:* r/{subreddit}
*Thread:* <{url}|{title}>
*Posted:* {time_ago}

*Pain points:* {pain_points}
*Angle:* {reply_angle}
⚠️ *Watch out:* {risk_flags or "None"}

---
*Draft reply:*
{draft_comment}

---
_Review and post manually at the link above._
```

---

## Infrastructure

**Minimal viable setup (Clement can build in ~1 day):**

```python
# Tech stack
- Python 3.11+
- praw (Reddit API)
- anthropic (Claude API)
- sqlite3 (deduplication — no Redis needed to start)
- requests (Slack webhook)
- schedule or APScheduler (cron)

# Run on
- Render background worker (already in your infra)
- OR a simple cron job on any VPS
- Runs every 30 minutes, costs ~$2–5/month in Claude API calls
```

**SQLite schema (deduplication):**
```sql
CREATE TABLE seen_threads (
  thread_id TEXT PRIMARY KEY,
  subreddit TEXT,
  title TEXT,
  score INTEGER,
  action TEXT,  -- 'replied', 'discarded', 'alerted'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Cost Estimate

| Component | Volume/month | Cost |
|---|---|---|
| Reddit API | Unlimited (free tier) | $0 |
| Claude scoring (Prompt #1) | ~2,000 threads × ~500 tokens | ~$1.50 |
| Claude drafting (Prompt #2) | ~200 threads × ~800 tokens | ~$1.00 |
| Slack webhook | Unlimited | $0 |
| **Total** | | **~$2.50/month** |

---

## What Itamar Does

1. Gets a Slack message with thread + draft
2. Reads the thread (link is there)
3. Edits the draft if needed (usually minor tweaks)
4. Pastes into Reddit manually
5. Optionally flags "replied" back (can be a Slack button later)

Total time per opportunity: **2–5 minutes.**

---

## Phase 2 (later)

- Slack "Mark as replied / Skip" buttons via Slack interactive components
- Track which comments got upvotes / replies (feedback loop on prompt quality)
- Expand to Hacker News, LinkedIn posts, Tenable Community forums
- Weekly digest: top 5 opportunities you missed (for retrospective)
