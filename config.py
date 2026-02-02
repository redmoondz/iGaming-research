"""Configuration for iGaming company analyzer."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Main configuration class."""

    # API
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 8192
    timeout: int = 180  # 3 min, due to web search

    # Rate Limiting
    web_search_rpm: int = 30
    initial_concurrency: int = 3
    max_concurrency: int = 10

    # Retry
    max_retries: int = 5
    base_delay: float = 2.0
    max_delay: float = 120.0

    # Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent)
    input_file: Path = field(default_factory=lambda: Path("data/input/companies.csv"))
    raw_output_dir: Path = field(default_factory=lambda: Path("data/raw"))
    output_dir: Path = field(default_factory=lambda: Path("data/output"))
    system_prompt_file: Path = field(default_factory=lambda: Path("prompts/system_prompt.txt"))

    # Tools
    tools: list = field(default_factory=list)

    # API Key (supports both ANTHROPIC_API_KEY and CLAUDE_API_TOKEN)
    api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_TOKEN")
    )

    def __post_init__(self):
        # Convert to absolute paths
        self.input_file = self.base_dir / self.input_file
        self.raw_output_dir = self.base_dir / self.raw_output_dir
        self.output_dir = self.base_dir / self.output_dir
        self.system_prompt_file = self.base_dir / self.system_prompt_file

        # Configure tools
        self.tools = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5  #TODO: adjust if prompt changes
        }]

    def validate(self) -> None:
        """Validate configuration and create directories."""
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Set it in environment or .env file."
            )

        if not self.system_prompt_file.exists():
            raise FileNotFoundError(
                f"System prompt not found: {self.system_prompt_file}"
            )

        if not self.input_file.exists():
            raise FileNotFoundError(
                f"Input CSV not found: {self.input_file}"
            )

        # Create output directories
        self.raw_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_system_prompt(self) -> str:
        """Load and return the system prompt."""
        return self.system_prompt_file.read_text(encoding="utf-8")


# CSV column mapping (adapt to actual input structure)
CSV_COLUMNS = {
    "company_name": "company_name",
    "website": "website",
    "type_of_business": "typeOfBusiness",
    "sector": "sector",
    "regions": "regionsOfOperation",
    "new_regions": "newRegionsTargeting",
}
