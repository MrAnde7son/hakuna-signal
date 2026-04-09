import logging
import time
from datetime import datetime, timezone

import schedule

import config
import db
import sources
from keyword_filter import should_process
from scorer import score_thread, _time_ago
from drafter import draft_comment
from report import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline():
    logger.info("Starting pipeline run")
    stats = {"fetched": 0, "filtered": 0, "scored": 0, "alerted": 0}
    opportunities = []

    for source_name, category, fetch in sources.iter_enabled_sources():
        logger.info("Scanning %s/%s", source_name, category)
        try:
            items = fetch(category)
        except Exception as e:
            logger.error("Failed to fetch %s/%s: %s", source_name, category, e)
            continue

        stats["fetched"] += len(items)

        for thread in items:
            # Dedup
            if db.is_seen(source_name, thread["id"]):
                continue

            # Keyword filter
            if not should_process(thread["title"], thread["body"], thread["score"], source_name, category):
                db.mark_seen(thread["id"], source_name, category, thread["title"], thread["score"], "filtered")
                stats["filtered"] += 1
                continue

            # Hydrate deferred fields. Reddit leaves top_comments=None in the
            # listing so we only pay the per-thread comment fetch for items
            # that survive dedup + filter (the bulk of the HTTP cost otherwise).
            if source_name == "reddit" and thread["top_comments"] is None:
                thread["top_comments"] = sources.reddit.fetch_comments(category, thread["id"])

            # Score. On transient failure (LLM blip, JSON parse), do NOT
            # mark seen — let the next run retry. Permanent stuck threads
            # will show up in the logs.
            result = score_thread(thread)
            if result is None:
                logger.warning("Skipping %s/%s thread %s — will retry next run",
                               source_name, category, thread["id"])
                continue

            stats["scored"] += 1
            score = result.get("score", 0)

            created_dt = datetime.fromtimestamp(thread["created_utc"], tz=timezone.utc)

            # Draft only for high-scoring Reddit threads. Other sources feed
            # the Intel tab only — replying to a Spiceworks/forum post is a
            # different workflow we don't automate yet.
            draft = None
            if score >= config.MIN_RELEVANCE_SCORE and source_name == "reddit":
                draft = draft_comment(thread, result)
                if draft is None:
                    # Transient drafter failure — same retry policy as scorer.
                    logger.warning("Drafting failed for %s thread %s — will retry next run",
                                   category, thread["id"])
                    continue
                stats["alerted"] += 1
                logger.info("Opportunity found: %s/%s thread %s (score %d)",
                            source_name, category, thread["id"], score)
            else:
                logger.info("%s/%s thread %s scored %d — no draft",
                            source_name, category, thread["id"], score)

            # Action label: "alerted" only for Reddit threads we drafted; for
            # non-Reddit sources or sub-threshold scores we record the LLM's
            # recommended_action so the dashboard intel tab can distinguish.
            if draft:
                action = "alerted"
            elif result.get("recommended_action") == "discard":
                action = "discarded"
            else:
                action = "low_score"
            opp = {
                "thread": thread,
                "scorer_result": result,
                "draft": draft or "",
                "time_ago": _time_ago(created_dt),
                "is_new": True,
            }
            opportunities.append(opp)
            db.mark_seen(thread["id"], source_name, category, thread["title"], thread["score"], action,
                         opportunity_data=opp)

    # Include previously-found but unviewed opportunities
    previous = db.get_unviewed_opportunities()
    new_thread_ids = {opp["thread"]["id"] for opp in opportunities}
    for prev_opp in previous:
        if prev_opp["thread"]["id"] not in new_thread_ids:
            prev_opp["is_new"] = False
            opportunities.append(prev_opp)

    # Sort: new first, then old
    opportunities.sort(key=lambda o: not o.get("is_new", True))

    # Update dashboard (intel is aggregated from full data.json inside generate_report)
    all_keys = [(opp["thread"].get("source", "reddit"), opp["thread"]["id"]) for opp in opportunities]
    dashboard = generate_report(opportunities)
    db.mark_viewed(all_keys)

    logger.info(
        "Pipeline complete — fetched=%d filtered=%d scored=%d alerted=%d",
        stats["fetched"], stats["filtered"], stats["scored"], stats["alerted"],
    )


def main():
    logger.info("Hakuna Signal starting (interval=%dm)", config.RUN_INTERVAL_MINUTES)

    # Run once immediately
    run_pipeline()

    # Then schedule
    schedule.every(config.RUN_INTERVAL_MINUTES).minutes.do(run_pipeline)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
