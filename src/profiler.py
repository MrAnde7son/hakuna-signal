"""Aggregate market intelligence from scored opportunities."""

from collections import Counter


def aggregate_profiles(opportunities: list[dict]) -> dict:
    """Build aggregate market intelligence from all opportunities.

    Takes a list of opportunity dicts (each containing a scorer_result with
    company_profile, tools_detected, pain_point_categories, team_functions)
    and returns aggregate statistics suitable for dashboard embedding.
    """
    tools = Counter()
    pain_categories = Counter()
    team_functions = Counter()
    employee_ranges = Counter()
    maturity_levels = Counter()
    industries = Counter()
    security_team_sizes = Counter()
    named_companies = []

    for opp in opportunities:
        sr = opp.get("scorer_result", {})

        # Tools
        for tool in sr.get("tools_detected", []):
            tools[tool] += 1

        # Pain point categories
        for cat in sr.get("pain_point_categories", []):
            pain_categories[cat] += 1

        # Team functions
        for fn in sr.get("team_functions", []):
            team_functions[fn] += 1

        # Company profile
        profile = sr.get("company_profile", {})
        if profile:
            if profile.get("employee_range"):
                employee_ranges[profile["employee_range"]] += 1
            if profile.get("maturity_level"):
                maturity_levels[profile["maturity_level"]] += 1
            if profile.get("industry"):
                industries[profile["industry"]] += 1
            if profile.get("security_team_size"):
                security_team_sizes[profile["security_team_size"]] += 1
            if profile.get("company_name"):
                named_companies.append({
                    "name": profile["company_name"],
                    "industry": profile.get("industry"),
                    "employee_range": profile.get("employee_range"),
                    "tools": sr.get("tools_detected", []),
                    "pain_points": sr.get("pain_point_categories", []),
                    "thread_url": opp.get("thread", {}).get("url", ""),
                })

    return {
        "total_threads_analyzed": len(opportunities),
        "tools": _counter_to_sorted_list(tools),
        "pain_point_categories": _counter_to_sorted_list(pain_categories),
        "team_functions": _counter_to_sorted_list(team_functions),
        "employee_ranges": _counter_to_sorted_list(employee_ranges),
        "maturity_levels": _counter_to_sorted_list(maturity_levels),
        "industries": _counter_to_sorted_list(industries),
        "security_team_sizes": _counter_to_sorted_list(security_team_sizes),
        "named_companies": named_companies,
    }


def _counter_to_sorted_list(counter: Counter) -> list[dict]:
    """Convert a Counter to a sorted list of {name, count} dicts."""
    return [{"name": name, "count": count}
            for name, count in counter.most_common()]
