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
        qual = result.get("qualification", {}) or {}
        profile = result.get("profile_data", {}) or {}
        meta = r.get("meta", {}) or {}
        usage = meta.get("usage", {}) or {}

        row = {
            # Meta
            "processed_at": meta.get("processed_at"),
            "processing_time_sec": meta.get("processing_time_sec"),
            "web_searches_used": usage.get("web_search_requests"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),

            # Basic info
            "company_name": result.get("company_name"),
            "website": result.get("website"),
            "linkedin_url": result.get("linkedin_url"),
            "research_date": result.get("research_date"),

            # Qualification
            "overall_qualified": qual.get("overall_qualified"),
            "legal_status": qual.get("legal_standing", {}).get("status") if qual.get("legal_standing") else None,
            "legal_details": qual.get("legal_standing", {}).get("details") if qual.get("legal_standing") else None,
            "portfolio_status": qual.get("game_portfolio", {}).get("status") if qual.get("game_portfolio") else None,
            "game_types": ", ".join(qual.get("game_portfolio", {}).get("game_types_found", []) or []) if qual.get("game_portfolio") else None,
            "business_functions_status": qual.get("business_functions", {}).get("status") if qual.get("business_functions") else None,
            "has_development": qual.get("business_functions", {}).get("development", {}).get("present") if qual.get("business_functions", {}).get("development") else None,
            "has_publishing": qual.get("business_functions", {}).get("publishing_marketing", {}).get("present") if qual.get("business_functions", {}).get("publishing_marketing") else None,
            "has_live_ops": qual.get("business_functions", {}).get("live_operations", {}).get("present") if qual.get("business_functions", {}).get("live_operations") else None,

            # Profile data (only present for qualified)
            "total_games": profile.get("portfolio_size", {}).get("total_games") if profile.get("portfolio_size") else None,
            "portfolio_confidence": profile.get("portfolio_size", {}).get("confidence") if profile.get("portfolio_size") else None,
            "games_last_2_years": profile.get("release_frequency", {}).get("games_last_2_years") if profile.get("release_frequency") else None,
            "employee_count": profile.get("company_size", {}).get("employee_count") if profile.get("company_size") else None,
            "revenue_usd": profile.get("revenue", {}).get("amount") if profile.get("revenue") else None,
            "revenue_source": profile.get("revenue", {}).get("source") if profile.get("revenue") else None,
            "works_with_external_studios": profile.get("external_partnerships", {}).get("works_with_external_studios") if profile.get("external_partnerships") else None,
            "eu_based_studios": profile.get("external_partnerships", {}).get("eu_based_studios") if profile.get("external_partnerships") else None,
            "has_external_funding": profile.get("funding", {}).get("has_external_funding") if profile.get("funding") else None,
            "public_company": profile.get("funding", {}).get("public_company") if profile.get("funding") else None,
            "has_art_team": profile.get("in_house_creative", {}).get("has_art_team") if profile.get("in_house_creative") else None,
            "art_team_size": profile.get("in_house_creative", {}).get("team_size_estimate") if profile.get("in_house_creative") else None,

            # Notes
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
