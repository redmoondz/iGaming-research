import os
import dotenv

dotenv.load_dotenv()

DATA_DIR = "data"
INPUT_DATA_DIR = f"{DATA_DIR}/input"
OUTPUT_DATA_DIR = f"{DATA_DIR}/output"
RAW_OUTPUT_DATA_DIR = f"{OUTPUT_DATA_DIR}/raw"
CONFIG_DIR = "config"

PROMPT_FILE = f"{CONFIG_DIR}/system_prompt.txt"

CLAUDE_API_TOKEN = os.getenv("CLAUDE_API_TOKEN")

TAGS = [
    "Supplier (product, technology or service)",
    "Operator (team involved in offering betting / games / slots to consumers)"
]

def validate_config():
   if not CLAUDE_API_TOKEN:
      raise ValueError("CLAUDE_API_TOKEN is not set in environment variables.")
    
   for i in ["DATA_DIR", "INPUT_DATA_DIR", "OUTPUT_DATA_DIR", "RAW_OUTPUT_DATA_DIR", "CONFIG_DIR"]:
      dir_path = globals()[i]
      if not os.path.exists(dir_path):
         os.makedirs(dir_path)
         print(f"Created directory: {dir_path}")
