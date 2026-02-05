#!/usr/bin/env python3
"""
iGaming Company Analyzer

Async script for analyzing ~1800 iGaming companies through Claude API with web search.
Two-stage analysis: qualification (Pass/Fail) → detailed profile (qualified only).
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

from anthropic import AsyncAnthropic

from config import Config
from file_utils import (
    IndexManager,
    ErrorLogger,
    load_csv,
    atomic_write_json,
    generate_company_filename,
)
from processor import process_company, result_to_dict
from rate_limiter import AsyncSlidingWindowRateLimiter, AdaptiveConcurrencyManager
from aggregator import aggregate_results, print_statistics


class ProgressTracker:
    """Track and display processing progress."""

    def __init__(self, total: int):
        self.total = total
        self.processed = 0
        self.qualified = 0
        self.failed = 0
        self.errors = 0
        self.total_searches = 0
        self.start_time = datetime.now(timezone.utc)

    def update(
        self,
        qualified: bool = False,
        searches: int = 0,
        error: bool = False
    ) -> None:
        """Update progress counters."""
        self.processed += 1
        self.total_searches += searches
        if error:
            self.errors += 1
        elif qualified:
            self.qualified += 1
        else:
            self.failed += 1

    def get_progress_str(self) -> str:
        """Get formatted progress string."""
        pct = (self.processed / self.total * 100) if self.total > 0 else 0
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        # Estimate remaining time
        if self.processed > 0:
            rate = self.processed / elapsed
            remaining = (self.total - self.processed) / rate if rate > 0 else 0
            eta_min = int(remaining / 60)
            eta_sec = int(remaining % 60)
            eta_str = f"{eta_min}m {eta_sec}s"
        else:
            eta_str = "calculating..."

        avg_searches = (
            self.total_searches / self.processed
            if self.processed > 0 else 0
        )

        return (
            f"Progress: {self.processed}/{self.total} ({pct:.1f}%) | "
            f"Qualified: {self.qualified} | "
            f"Failed: {self.failed} | "
            f"Errors: {self.errors} | "
            f"Searches: {self.total_searches} (avg: {avg_searches:.1f}) | "
            f"ETA: {eta_str}"
        )


async def process_batch(
    companies: List[Dict[str, str]],
    config: Config,
    index_manager: IndexManager,
    error_logger: ErrorLogger,
    dry_run: bool = False,
) -> None:
    """
    Process a batch of companies concurrently.

    Args:
        companies: List of company dicts to process.
        config: Configuration object.
        index_manager: Index manager for tracking progress.
        error_logger: Error logger for failures.
        dry_run: If True, don't make API calls.
    """
    if not companies:
        print("No companies to process.")
        return

    system_prompt = config.load_system_prompt()
    client = AsyncAnthropic(api_key=config.api_key)

    rate_limiter = AsyncSlidingWindowRateLimiter(max_rpm=config.web_search_rpm)
    concurrency_manager = AdaptiveConcurrencyManager(
        initial_concurrency=config.initial_concurrency,
        max_concurrency=config.max_concurrency,
        max_rpm=config.web_search_rpm,
    )
    semaphore = asyncio.Semaphore(config.initial_concurrency)

    progress = ProgressTracker(len(companies))

    print(f"\nStarting processing of {len(companies)} companies...")
    print(f"Model: {config.model}")
    print(f"Initial concurrency: {config.initial_concurrency}")
    print(f"Max concurrency: {config.max_concurrency}")
    print(f"Web search RPM limit: {config.web_search_rpm}")
    print()

    async def process_single(company: Dict[str, str]) -> None:
        """Process a single company and save result."""
        company_name = company.get("company_name", "Unknown")

        if dry_run:
            print(f"[DRY RUN] Would process: {company_name}")
            progress.update(qualified=False, searches=0)
            return

        # Dynamically adjust semaphore based on concurrency manager
        current_limit = await concurrency_manager.get_semaphore_value()
        # Note: We can't dynamically resize asyncio.Semaphore,
        # but the rate limiter handles the actual throttling

        result = await process_company(
            company=company,
            client=client,
            rate_limiter=rate_limiter,
            semaphore=semaphore,
            config=config,
            system_prompt=system_prompt,
            concurrency_manager=concurrency_manager,
        )

        # Save result
        filename = generate_company_filename(
            company_name,
            company.get("website")
        )
        filepath = config.raw_output_dir / f"{filename}.json"
        atomic_write_json(filepath, result_to_dict(result))

        # Update index
        index_manager.add(company_name, f"{filename}.json")

        # Update progress
        searches = result.meta.get("usage", {}).get("web_search_requests", 0)
        is_qualified = (
            result.result.get("qualification", {}).get("overall_qualified", False)
            if result.result else False
        )

        if result.success:
            progress.update(qualified=is_qualified, searches=searches)
        else:
            progress.update(error=True, searches=searches)
            error_logger.add(
                company_name=company_name,
                error_type="processing_error",
                error_message=result.error or "Unknown error",
                timestamp=result.meta.get("processed_at", ""),
            )

        # Print progress
        print(f"\r{progress.get_progress_str()}", end="", flush=True)

    # Warm up cache with first request (sequential)
    if companies and not dry_run:
        print("Warming up prompt cache...")
        await process_single(companies[0])
        companies = companies[1:]

    # Process remaining companies concurrently with semaphore limiting
    if companies:
        tasks = [process_single(company) for company in companies]
        await asyncio.gather(*tasks, return_exceptions=True)

    print()  # New line after progress
    print("\nProcessing complete!")


def filter_unprocessed(
    companies: List[Dict[str, str]],
    index_manager: IndexManager,
) -> List[Dict[str, str]]:
    """Filter out already processed companies."""
    processed = index_manager.get_all_processed()
    return [
        c for c in companies
        if c.get("company_name") not in processed
    ]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze iGaming companies using Claude API with web search"
    )

    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="Only aggregate existing results, don't process new companies"
    )

    parser.add_argument(
        "--companies",
        type=str,
        help="Comma-separated list of specific companies to process"
    )

    parser.add_argument(
        "--test-run",
        type=int,
        metavar="N",
        help="Process only first N companies for testing"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate processing without making API calls"
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override initial concurrency setting"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from previous run (default: True)"
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, ignoring previous progress"
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Initialize config
    config = Config()

    # Override concurrency if specified
    if args.concurrency:
        config.initial_concurrency = args.concurrency

    # Validate config
    try:
        config.validate()
    except (ValueError, FileNotFoundError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    # Initialize managers
    index_manager = IndexManager(config.raw_output_dir)
    error_logger = ErrorLogger(config.raw_output_dir)

    # Handle aggregate-only mode
    if args.aggregate_only:
        print("Running aggregation only...")
        stats = aggregate_results(config)
        print_statistics(stats)
        return 0

    # Load companies from CSV
    companies = load_csv(config.input_file)
    if not companies:
        print(f"No companies found in {config.input_file}", file=sys.stderr)
        return 1

    print(f"Loaded {len(companies)} companies from CSV")

    # Filter specific companies if requested
    if args.companies:
        company_names = set(name.strip() for name in args.companies.split(","))
        companies = [c for c in companies if c.get("company_name") in company_names]
        print(f"Filtered to {len(companies)} specified companies")

    # Filter out already processed (unless --no-resume)
    if not args.no_resume:
        original_count = len(companies)
        companies = filter_unprocessed(companies, index_manager)
        skipped = original_count - len(companies)
        if skipped > 0:
            print(f"Resuming: skipping {skipped} already processed companies")

    # Limit for test run
    if args.test_run:
        companies = companies[:args.test_run]
        print(f"Test run: processing only {len(companies)} companies")

    # Remove duplicates by company_name (keep first occurrence)
    seen = set()
    unique_companies = []
    for c in companies:
        name = c.get("company_name")
        if name and name not in seen:
            seen.add(name)
            unique_companies.append(c)
    companies = unique_companies

    if not companies:
        print("All companies already processed. Use --no-resume to start fresh.")
        print("\nRunning aggregation...")
        stats = aggregate_results(config)
        print_statistics(stats)
        return 0

    # Run processing
    asyncio.run(
        process_batch(
            companies=companies,
            config=config,
            index_manager=index_manager,
            error_logger=error_logger,
            dry_run=args.dry_run,
        )
    )

    # Aggregate results
    print("\nAggregating results...")
    stats = aggregate_results(config)
    print_statistics(stats)

    return 0


if __name__ == "__main__":
    sys.exit(main())
