"""Aggregation of raw results into final output files."""

from pathlib import Path
from typing import Any, Dict, List

from config import Config
from file_utils import atomic_write_json, load_json, save_csv


def flatten_for_csv(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flatten nested JSON to flat structure for CSV export.

    Args:
        results: List of raw result dictionaries.

    Returns:
        List of flattened row dictionaries.
    """
    rows = []

    for r in results:
        result = r.get("result", {}) or {}
        classification = result.get("company_classification", {}) or {}
        qual = result.get("qualification", {}) or {}
        profile = result.get("profile_data", {}) or {}
        meta = r.get("meta", {}) or {}
        usage = meta.get("usage", {}) or {}

        legal = qual.get("legal_standing") or {}
        game_portfolio = qual.get("game_portfolio") or {}
        portfolio_size = profile.get("portfolio_size") or {}
        release_frequency = profile.get("release_frequency") or {}
        company_size = profile.get("company_size") or {}
        revenue = profile.get("revenue") or {}
        external_partnerships = profile.get("external_partnerships") or {}
        funding = profile.get("funding") or {}
        in_house_creative = profile.get("in_house_creative") or {}

        row = {
            # Basic info
            "company_name": result.get("company_name"),
            "website": result.get("website"),
            "linkedin_url": result.get("linkedin_url"),

            # Company Classification
            "company_type": classification.get("type"),
            "classification_details": classification.get("details"),

            # Qualification — Legal
            "legal_status": legal.get("status"),
            "legal_details": legal.get("details"),

            # Qualification — Game Portfolio
            "portfolio_status": game_portfolio.get("status"),
            "game_portfolio_details": game_portfolio.get("details"),
            "game_types": ", ".join(game_portfolio.get("game_types_found", []) or []),

            # Qualification — Overall
            "overall_qualified": qual.get("overall_qualified"),

            # Profile — Portfolio Size
            "total_games": portfolio_size.get("total_games"),
            "total_games_description": portfolio_size.get("total_games_description"),

            # Profile — Release Frequency
            "games_last_2_years": release_frequency.get("games_last_2_years"),
            "release_frequency_description": release_frequency.get("description"),
            "recent_titles": release_frequency.get("recent_titles"),

            # Profile — Company Size
            "employee_count": company_size.get("employee_count"),

            # Profile — Revenue
            "revenue_usd": revenue.get("amount"),
            "revenue_details": revenue.get("details"),

            # Profile — External Partnerships
            "works_with_external_studios": external_partnerships.get("works_with_external_studios"),
            "eu_based_studios": external_partnerships.get("eu_based_studios"),
            "external_partnerships_details": external_partnerships.get("details"),

            # Profile — Funding
            "has_external_funding": funding.get("has_external_funding"),
            "funding_rounds": funding.get("funding_rounds"),
            "public_company": funding.get("public_company"),

            # Profile — In-House Creative
            "has_art_team": in_house_creative.get("has_art_team"),
            "has_video_production": in_house_creative.get("has_video_production"),
            "art_team_size": in_house_creative.get("team_size_estimate"),
            "in_house_creative_evidence": in_house_creative.get("evidence"),

            # Meta
            "processed_at": meta.get("processed_at"),
            "processing_time_sec": meta.get("processing_time_sec"),
            "web_searches_used": usage.get("web_search_requests"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),

            # Notes
            "research_date": result.get("research_date"),
            "research_notes": result.get("research_notes"),
            "data_gaps": ", ".join(result.get("data_gaps", []) or []),
        }
        rows.append(row)

    return rows


def aggregate_results(config: Config) -> Dict[str, Any]:
    """
    Aggregate all raw results into final output files.

    Args:
        config: Configuration object.

    Returns:
        Statistics dictionary.
    """
    raw_dir = config.raw_output_dir
    output_dir = config.output_dir

    all_results: List[Dict] = []
    qualified: List[Dict] = []
    disqualified: List[Dict] = []
    errors: List[Dict] = []

    # Load all JSON files from raw directory
    for json_file in raw_dir.glob("*.json"):
        # Skip index and error files
        if json_file.name.startswith("_"):
            continue

        data = load_json(json_file)
        if not data:
            continue

        all_results.append(data)

        # Check for errors
        if data.get("error"):
            errors.append(data)
            continue

        # Classify by qualification status
        result = data.get("result", {})
        is_qualified = (
            result.get("qualification", {}).get("overall_qualified", False)
            if result else False
        )

        if is_qualified:
            qualified.append(data)
        else:
            disqualified.append(data)

    # Save JSON files
    atomic_write_json(output_dir / "full_results.json", all_results)
    atomic_write_json(output_dir / "qualified.json", qualified)
    atomic_write_json(output_dir / "disqualified.json", disqualified)

    if errors:
        atomic_write_json(output_dir / "errors.json", errors)

    # Generate CSV for qualified companies
    if qualified:
        csv_rows = flatten_for_csv(qualified)
        save_csv(output_dir / "qualified.csv", csv_rows)

    # Generate CSV for all results
    if all_results:
        all_csv_rows = flatten_for_csv(all_results)
        save_csv(output_dir / "all_results.csv", all_csv_rows)

    # Calculate statistics
    total = len(all_results)
    stats = {
        "total": total,
        "qualified": len(qualified),
        "disqualified": len(disqualified),
        "errors": len(errors),
        "qualification_rate": f"{len(qualified)/total*100:.1f}%" if total > 0 else "0%",
    }

    # Calculate usage statistics
    total_searches = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read = 0

    for r in all_results:
        usage = r.get("meta", {}).get("usage", {})
        total_searches += usage.get("web_search_requests", 0)
        total_input_tokens += usage.get("input_tokens", 0)
        total_output_tokens += usage.get("output_tokens", 0)
        total_cache_read += usage.get("cache_read_tokens", 0)

    stats["usage"] = {
        "total_web_searches": total_searches,
        "avg_searches_per_company": round(total_searches / total, 2) if total > 0 else 0,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read,
    }

    # Save statistics
    atomic_write_json(output_dir / "statistics.json", stats)

    return stats


def print_statistics(stats: Dict[str, Any]) -> None:
    """Print formatted statistics to console."""
    print("\n" + "=" * 50)
    print("AGGREGATION COMPLETE")
    print("=" * 50)
    print(f"Total companies processed: {stats['total']}")
    print(f"Qualified: {stats['qualified']} ({stats['qualification_rate']})")
    print(f"Disqualified: {stats['disqualified']}")
    print(f"Errors: {stats['errors']}")
    print()

    usage = stats.get("usage", {})
    print("Usage Statistics:")
    print(f"  Total web searches: {usage.get('total_web_searches', 0)}")
    print(f"  Avg searches/company: {usage.get('avg_searches_per_company', 0)}")
    print(f"  Total input tokens: {usage.get('total_input_tokens', 0):,}")
    print(f"  Total output tokens: {usage.get('total_output_tokens', 0):,}")
    print(f"  Cache read tokens: {usage.get('total_cache_read_tokens', 0):,}")
    print("=" * 50)
