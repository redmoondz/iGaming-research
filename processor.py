"""Company processing logic with Claude API."""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic, APIError, RateLimitError

from config import Config
from rate_limiter import AsyncSlidingWindowRateLimiter, AdaptiveConcurrencyManager


@dataclass
class ProcessingResult:
    """Result of processing a single company."""

    success: bool
    company_name: str
    meta: Dict[str, Any] = field(default_factory=dict)
    input_data: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None  # For debugging failed parses


def format_company_input(company: Dict[str, str]) -> str:
    """
    Format company data for the API request.

    Args:
        company: Company data from CSV.

    Returns:
        Formatted input text for Claude.
    """
    parts = [f"## Company to Analyze\n\n**Company Name:** {company['company_name']}"]

    if company.get("website"):
        website = company["website"]
        # Clean up website URL
        if website and not website.startswith(("http://", "https://", "mailto:")):
            website = f"https://{website}"
        if website and not website.startswith("mailto:"):
            parts.append(f"**Website:** {website}")

    if company.get("linkedin_url"):
        parts.append(f"**LinkedIn:** {company['linkedin_url']}")

    # Add additional context from CSV columns
    additional = []
    if company.get("typeOfBusiness"):
        additional.append(f"Business Type: {company['typeOfBusiness']}")
    if company.get("sector"):
        additional.append(f"Sector: {company['sector']}")
    if company.get("regionsOfOperation"):
        additional.append(f"Operating Regions: {company['regionsOfOperation']}")

    if additional:
        parts.append(f"**Additional Context:** {'; '.join(additional)}")

    parts.append("\nConduct the analysis and return ONLY the raw JSON object. No text before or after.")
    return "\n".join(parts)


def clean_json_string(json_str: str) -> str:
    """
    Clean JSON string by removing common issues.

    Args:
        json_str: Raw JSON string.

    Returns:
        Cleaned JSON string.
    """
    # Remove trailing commas before } or ]
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    # Remove JavaScript-style comments
    json_str = re.sub(r'//.*?$', '', json_str, flags=re.MULTILINE)
    json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
    return json_str


def extract_json_from_response(response_text: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON from Claude's response.

    Handles both plain JSON and markdown code blocks.
    Attempts multiple parsing strategies.

    Args:
        response_text: Raw response text from Claude.

    Returns:
        Parsed JSON dict or None if parsing fails.
    """
    if not response_text:
        return None

    json_str = None

    # Strategy 1: Find JSON in markdown code blocks
    json_match = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n```', response_text)
    if json_match:
        json_str = json_match.group(1).strip()

    # Strategy 2: Find JSON object starting with { and ending with }
    if not json_str:
        # Find the first { and last } to extract the full JSON object
        first_brace = response_text.find('{')
        last_brace = response_text.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_str = response_text[first_brace:last_brace + 1]

    if not json_str:
        return None

    # Try parsing with cleaning
    for attempt_clean in [False, True]:
        try:
            text_to_parse = clean_json_string(json_str) if attempt_clean else json_str
            return json.loads(text_to_parse)
        except json.JSONDecodeError:
            continue

    # Strategy 3: Try to find balanced braces
    try:
        first_brace = json_str.find('{')
        if first_brace != -1:
            depth = 0
            end_pos = first_brace
            for i, char in enumerate(json_str[first_brace:], first_brace):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_pos = i
                        break
            balanced_json = json_str[first_brace:end_pos + 1]
            return json.loads(clean_json_string(balanced_json))
    except json.JSONDecodeError:
        pass

    return None


def validate_response(response: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate Claude's response structure (V7 format).

    Args:
        response: Parsed JSON response.

    Returns:
        Tuple of (is_valid, list of errors).
    """
    errors = []

    # Required top-level fields
    required_top = ["company_name", "research_date", "company_classification", "qualification"]
    for field_name in required_top:
        if field_name not in response:
            errors.append(f"Missing required field: {field_name}")

    # Check company_classification structure
    classification = response.get("company_classification", {})
    if "type" not in classification:
        errors.append("Missing company_classification.type")

    # Check qualification structure
    qual = response.get("qualification", {})
    if "overall_qualified" not in qual:
        errors.append("Missing qualification.overall_qualified")

    # If qualified=True, must have profile_data (unless NOT_RELEVANT)
    is_not_relevant = classification.get("type") == "NOT_RELEVANT"
    if qual.get("overall_qualified") and "profile_data" not in response and not is_not_relevant:
        errors.append("Qualified company missing profile_data")

    return len(errors) == 0, errors


def get_response_text(response) -> str:
    """Extract text content from API response.

    When web_search is used, the response contains multiple content blocks:
    - Text blocks (model's commentary)
    - Tool_use blocks (web_search calls)
    - Tool_result blocks (search results)

    We concatenate all text blocks to capture the full response,
    including the final JSON output that comes after searches.
    """
    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    return "\n".join(text_parts)


def get_usage_stats(response) -> Dict[str, int]:
    """Extract usage statistics from API response."""
    usage = response.usage
    stats = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }

    # Get web search requests from server_tool_use
    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use:
        stats["web_search_requests"] = getattr(server_tool_use, "web_search_requests", 0) or 0
    else:
        stats["web_search_requests"] = 0

    return stats


async def call_api_with_retry(
    client: AsyncAnthropic,
    messages: List[Dict],
    config: Config,
    system_prompt: Optional[str] = None,
    max_retries: Optional[int] = None
) -> Any:
    """
    Call Claude API with exponential backoff retry.

    Args:
        client: Anthropic async client.
        messages: Messages to send.
        config: Configuration object.
        system_prompt: System prompt with cache control for efficient caching.
        max_retries: Override max retries.

    Returns:
        API response.

    Raises:
        APIError: If all retries exhausted.
    """
    retries = max_retries or config.max_retries
    delay = config.base_delay

    # Build system parameter with cache control if provided
    system = None
    if system_prompt:
        system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]

    for attempt in range(retries):
        try:
            create_kwargs = {
                "model": config.model,
                "max_tokens": config.max_tokens,
                "tools": config.tools,
                "messages": messages,
            }
            if system:
                create_kwargs["system"] = system

            response = await client.messages.create(**create_kwargs)
            return response

        except RateLimitError as e:
            if attempt == retries - 1:
                raise
            # Use longer delay for rate limits
            wait_time = min(delay * (2 ** attempt), config.max_delay)
            await asyncio.sleep(wait_time)

        except APIError as e:
            if attempt == retries - 1:
                raise
            # Check if retryable
            if e.status_code in (500, 502, 503, 529):
                wait_time = min(delay * (2 ** attempt), config.max_delay)
                await asyncio.sleep(wait_time)
            else:
                raise


async def retry_for_valid_json(
    client: AsyncAnthropic,
    company: Dict[str, str],
    errors: List[str],
    config: Config,
    system_prompt: str,
    original_response: str,
) -> Optional[Dict[str, Any]]:
    """
    Retry API call to get valid JSON.

    Args:
        client: Anthropic async client.
        company: Company data.
        errors: Validation errors from previous attempt.
        config: Configuration object.
        system_prompt: System prompt text.
        original_response: Original invalid response.

    Returns:
        Valid JSON dict or None.
    """
    retry_prompt = f"""
Your previous response could not be parsed as valid JSON.

Issues found:
{chr(10).join(f'- {e}' for e in errors)}

IMPORTANT: Return ONLY the raw JSON object for {company['company_name']}.
- Start directly with {{ (opening brace)
- End with }} (closing brace)
- NO markdown code blocks (no ```)
- NO text before or after the JSON
- Ensure all strings are properly quoted
- No trailing commas before }} or ]]
"""

    messages = [
        {
            "role": "user",
            "content": format_company_input(company)
        },
        {
            "role": "assistant",
            "content": original_response
        },
        {
            "role": "user",
            "content": retry_prompt
        }
    ]

    try:
        response = await call_api_with_retry(client, messages, config, system_prompt=system_prompt, max_retries=2)
        text = get_response_text(response)
        return extract_json_from_response(text)
    except Exception:
        return None


async def process_company(
    company: Dict[str, str],
    client: AsyncAnthropic,
    rate_limiter: AsyncSlidingWindowRateLimiter,
    semaphore: asyncio.Semaphore,
    config: Config,
    system_prompt: str,
    concurrency_manager: Optional[AdaptiveConcurrencyManager] = None,
) -> ProcessingResult:
    """
    Process a single company through the analysis pipeline.

    Args:
        company: Company data from CSV.
        client: Anthropic async client.
        rate_limiter: Rate limiter for web search.
        semaphore: Concurrency semaphore.
        config: Configuration object.
        system_prompt: System prompt text.
        concurrency_manager: Optional adaptive concurrency manager.

    Returns:
        ProcessingResult with success/failure and data.
    """
    company_name = company.get("company_name", "Unknown")

    async with semaphore:
        start_time = time.time()

        try:
            # Wait for rate limiter
            await rate_limiter.acquire(estimated_searches=7)

            # Build message with company data only (system prompt passed separately for caching)
            messages = [{
                "role": "user",
                "content": format_company_input(company)
            }]

            # Call API with system prompt as separate parameter for proper caching
            response = await call_api_with_retry(client, messages, config, system_prompt=system_prompt)

            # Extract usage and update rate limiter
            usage_stats = get_usage_stats(response)
            web_searches = usage_stats.get("web_search_requests", 0)
            await rate_limiter.consume(web_searches)

            # Update concurrency manager if present
            if concurrency_manager:
                await concurrency_manager.record_searches(web_searches)

            # Extract and parse response
            response_text = get_response_text(response)
            result_json = extract_json_from_response(response_text)

            # If JSON extraction failed completely, try retry
            if not result_json and response_text:
                result_json = await retry_for_valid_json(
                    client, company,
                    ["Could not extract JSON from response"],
                    config, system_prompt, response_text
                )

            # Validate response
            if result_json:
                is_valid, errors = validate_response(result_json)
                if not is_valid:
                    # Try to fix JSON with validation errors
                    fixed_json = await retry_for_valid_json(
                        client, company, errors, config, system_prompt, response_text
                    )
                    if fixed_json:
                        result_json = fixed_json

            processing_time = time.time() - start_time

            if not result_json:
                return ProcessingResult(
                    success=False,
                    company_name=company_name,
                    meta={
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "model": config.model,
                        "processing_time_sec": round(processing_time, 2),
                        "usage": usage_stats,
                    },
                    input_data=company,
                    error="Failed to extract valid JSON from response",
                    raw_response=response_text[:5000] if response_text else None,  # Truncate for storage
                )

            return ProcessingResult(
                success=True,
                company_name=company_name,
                meta={
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "model": config.model,
                    "processing_time_sec": round(processing_time, 2),
                    "usage": usage_stats,
                },
                input_data=company,
                result=result_json,
            )

        except Exception as e:
            processing_time = time.time() - start_time
            return ProcessingResult(
                success=False,
                company_name=company_name,
                meta={
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "model": config.model,
                    "processing_time_sec": round(processing_time, 2),
                },
                input_data=company,
                error=str(e),
            )


def result_to_dict(result: ProcessingResult) -> Dict[str, Any]:
    """Convert ProcessingResult to dictionary for JSON serialization."""
    data = {
        "meta": result.meta,
        "input": result.input_data,
        "result": result.result,
        "error": result.error,
    }
    # Include raw_response only for failed parses (for debugging)
    if result.raw_response:
        data["raw_response"] = result.raw_response
    return data
