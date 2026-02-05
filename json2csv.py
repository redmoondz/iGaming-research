#!/usr/bin/env python3
"""
Script to convert JSON files from data/raw/ to a CSV table.
Reads all JSON files that don't start with "_" and flattens the structure.
"""

import json
import csv
import os
from pathlib import Path
from typing import Any


def flatten_dict(d: dict, parent_key: str = '', sep: str = '.') -> dict:
    """Recursively flatten a nested dictionary."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        elif isinstance(v, list):
            # Convert lists to string representation
            items.append((new_key, json.dumps(v, ensure_ascii=False) if v else ''))
        else:
            items.append((new_key, v))
    return dict(items)


def make_readable_header(key: str) -> str:
    """Convert dot-notation key to readable header."""
    header_mapping = {
        # Meta fields
        'meta.processed_at': 'Processed At',
        'meta.model': 'AI Model',
        'meta.processing_time_sec': 'Processing Time (sec)',
        'meta.usage.input_tokens': 'Input Tokens',
        'meta.usage.output_tokens': 'Output Tokens',
        'meta.usage.cache_read_tokens': 'Cache Read Tokens',
        'meta.usage.cache_creation_tokens': 'Cache Creation Tokens',
        'meta.usage.web_search_requests': 'Web Search Requests',

        # Input fields
        'input.company_name': 'Input Company Name',
        'input.': 'Input Extra',

        # Result basic fields
        'result.company_name': 'Company Name',
        'result.website': 'Website',
        'result.linkedin_url': 'LinkedIn URL',
        'result.research_date': 'Research Date',

        # Classification
        'result.company_classification.type': 'Company Type',
        'result.company_classification.sub_type': 'Company Sub-Type',
        'result.company_classification.details': 'Classification Details',
        'result.company_classification.service_relevance': 'Service Relevance',

        # Qualification - Legal Standing
        'result.qualification.legal_standing.status': 'Legal Status',
        'result.qualification.legal_standing.details': 'Legal Details',
        'result.qualification.legal_standing.sources': 'Legal Sources',

        # Qualification - Game Portfolio
        'result.qualification.game_portfolio.status': 'Game Portfolio Status',
        'result.qualification.game_portfolio.game_types_found': 'Game Types Found',
        'result.qualification.game_portfolio.details': 'Game Portfolio Details',
        'result.qualification.game_portfolio.sources': 'Game Portfolio Sources',

        # Qualification overall
        'result.qualification.overall_qualified': 'Overall Qualified',

        # Profile - Portfolio Size
        'result.profile_data.portfolio_size.total_games': 'Total Games',
        'result.profile_data.portfolio_size.total_games_description': 'Total Games Description',
        'result.profile_data.portfolio_size.confidence': 'Portfolio Size Confidence',
        'result.profile_data.portfolio_size.source': 'Portfolio Size Source',

        # Profile - Release Frequency
        'result.profile_data.release_frequency.games_last_2_years': 'Games Last 2 Years',
        'result.profile_data.release_frequency.description': 'Release Frequency Description',
        'result.profile_data.release_frequency.recent_titles': 'Recent Titles',
        'result.profile_data.release_frequency.confidence': 'Release Frequency Confidence',
        'result.profile_data.release_frequency.source': 'Release Frequency Source',

        # Profile - Company Size
        'result.profile_data.company_size.employee_count': 'Employee Count',
        'result.profile_data.company_size.source': 'Company Size Source',

        # Profile - Revenue
        'result.profile_data.revenue.amount': 'Revenue Amount',
        'result.profile_data.revenue.source': 'Revenue Source',
        'result.profile_data.revenue.details': 'Revenue Details',

        # Profile - External Partnerships
        'result.profile_data.external_partnerships.works_with_external_studios': 'Works With External Studios',
        'result.profile_data.external_partnerships.eu_based_studios': 'EU Based Studios',
        'result.profile_data.external_partnerships.details': 'External Partnerships Details',
        'result.profile_data.external_partnerships.sources': 'External Partnerships Sources',

        # Profile - Funding
        'result.profile_data.funding.has_external_funding': 'Has External Funding',
        'result.profile_data.funding.funding_rounds': 'Funding Rounds',
        'result.profile_data.funding.public_company': 'Public Company',
        'result.profile_data.funding.sources': 'Funding Sources',

        # Profile - In-House Creative
        'result.profile_data.in_house_creative.has_art_team': 'Has Art Team',
        'result.profile_data.in_house_creative.has_video_production': 'Has Video Production',
        'result.profile_data.in_house_creative.team_size_estimate': 'Creative Team Size Estimate',
        'result.profile_data.in_house_creative.likely_needs_external_support': 'Likely Needs External Support',
        'result.profile_data.in_house_creative.evidence': 'In-House Creative Evidence',
        'result.profile_data.in_house_creative.sources': 'In-House Creative Sources',

        # Research notes and gaps
        'result.research_notes': 'Research Notes',
        'result.data_gaps': 'Data Gaps',

        # Error
        'error': 'Error',
    }

    return header_mapping.get(key, key.replace('.', ' > ').replace('_', ' ').title())


def load_json_files(raw_dir: Path) -> list[dict]:
    """Load all JSON files that don't start with underscore."""
    records = []

    for filepath in sorted(raw_dir.glob('*.json')):
        # Skip files starting with underscore
        if filepath.name.startswith('_'):
            print(f"  Skipping: {filepath.name}")
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Add source filename
            data['_source_file'] = filepath.name
            records.append(data)

        except json.JSONDecodeError as e:
            print(f"  Error parsing {filepath.name}: {e}")
        except Exception as e:
            print(f"  Error reading {filepath.name}: {e}")

    return records


def main():
    # Paths
    script_dir = Path(__file__).parent
    raw_dir = script_dir / 'data' / 'raw'
    output_file = script_dir / 'data' / 'output' / 'companies_research.csv'

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reading JSON files from: {raw_dir}")
    records = load_json_files(raw_dir)
    print(f"Loaded {len(records)} records")

    if not records:
        print("No records to process!")
        return

    # Flatten all records
    print("Flattening JSON structures...")
    flat_records = []
    all_keys = set()

    for record in records:
        flat = flatten_dict(record)
        flat_records.append(flat)
        all_keys.update(flat.keys())

    # Define column order (prioritize important fields first)
    priority_keys = [
        '_source_file',
        'result.company_name',
        'result.website',
        'result.linkedin_url',
        'result.company_classification.type',
        'result.company_classification.sub_type',
        'result.qualification.overall_qualified',
        'result.qualification.legal_standing.status',
        'result.qualification.game_portfolio.status',
        'result.profile_data.company_size.employee_count',
        'result.profile_data.revenue.amount',
        'result.profile_data.portfolio_size.total_games',
        'result.profile_data.funding.has_external_funding',
        'result.profile_data.external_partnerships.works_with_external_studios',
        'result.profile_data.in_house_creative.has_art_team',
        'result.profile_data.in_house_creative.has_video_production',
        'result.profile_data.in_house_creative.likely_needs_external_support',
    ]

    # Order columns: priority first, then alphabetically for the rest
    ordered_keys = []
    for key in priority_keys:
        if key in all_keys:
            ordered_keys.append(key)
            all_keys.discard(key)

    # Add remaining keys sorted
    ordered_keys.extend(sorted(all_keys))

    # Create readable headers
    headers = [make_readable_header(key) for key in ordered_keys]

    # Write CSV
    print(f"Writing CSV to: {output_file}")
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for flat_record in flat_records:
            row = []
            for key in ordered_keys:
                value = flat_record.get(key, '')
                # Convert None to empty string
                if value is None:
                    value = ''
                # Convert booleans to readable format
                elif isinstance(value, bool):
                    value = 'Yes' if value else 'No'
                row.append(value)
            writer.writerow(row)

    print(f"Done! Created CSV with {len(flat_records)} rows and {len(headers)} columns")
    print(f"\nFirst 10 columns:")
    for i, header in enumerate(headers[:10], 1):
        print(f"  {i}. {header}")


if __name__ == '__main__':
    main()
