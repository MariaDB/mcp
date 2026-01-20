# config.py
import os
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Load environment variables from .env file
load_dotenv()

# --- Logging Configuration ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = os.getenv("LOG_FILE", "logs/mcp_server.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 10 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))

_allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if _allowed_origins_env:
    ALLOWED_ORIGINS: list[str] = _allowed_origins_env.split(",")
else:
    ALLOWED_ORIGINS = ["http://localhost", "http://127.0.0.1", "http://*", "https://localhost", "https://127.0.0.1", "vscode-file://vscode-app"]

_allowed_hosts_env = os.getenv("ALLOWED_HOSTS")
if _allowed_hosts_env:
    ALLOWED_HOSTS: list[str] = _allowed_hosts_env.split(",")
else:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Create formatter
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Remove existing handlers to avoid duplication if script is reloaded
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# File Handler - Ensure log directory exists
log_file = Path(LOG_FILE_PATH)
log_file.parent.mkdir(parents=True, exist_ok=True)

file_handler = RotatingFileHandler(
    log_file,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# The specific logger used in server.py and elsewhere will inherit this configuration.
logger = logging.getLogger(__name__)

# --- Database Configuration ---
# Support multiple databases via comma-separated values
DB_HOSTS = os.getenv("DB_HOSTS", os.getenv("DB_HOST", "localhost")).split(",")
DB_PORTS = [int(p) for p in os.getenv("DB_PORTS", os.getenv("DB_PORT", "3306")).split(",")]
DB_USERS = os.getenv("DB_USERS", os.getenv("DB_USER", "")).split(",")
DB_PASSWORDS = os.getenv("DB_PASSWORDS", os.getenv("DB_PASSWORD", "")).split(",")
DB_NAMES = os.getenv("DB_NAMES", os.getenv("DB_NAME", "")).split(",")
DB_CHARSETS = os.getenv("DB_CHARSETS", os.getenv("DB_CHARSET", "")).split(",")

# Validate multiple database configuration - arrays should match in length
if len(DB_HOSTS) > 1:
    _min_len = min(len(DB_HOSTS), len(DB_USERS), len(DB_PASSWORDS))
    if len(DB_HOSTS) != len(DB_USERS) or len(DB_HOSTS) != len(DB_PASSWORDS):
        logger.warning(
            f"Multiple database config length mismatch: "
            f"DB_HOSTS={len(DB_HOSTS)}, DB_USERS={len(DB_USERS)}, DB_PASSWORDS={len(DB_PASSWORDS)}. "
            f"Using first {_min_len} entries."
        )
        DB_HOSTS = DB_HOSTS[:_min_len]
        DB_USERS = DB_USERS[:_min_len]
        DB_PASSWORDS = DB_PASSWORDS[:_min_len]

# Legacy single database support
DB_HOST = DB_HOSTS[0]
DB_PORT = DB_PORTS[0] if DB_PORTS else 3306
DB_USER = DB_USERS[0] if DB_USERS else None
DB_PASSWORD = DB_PASSWORDS[0] if DB_PASSWORDS else None
DB_NAME = DB_NAMES[0] if DB_NAMES else None
DB_CHARSET = DB_CHARSETS[0] if DB_CHARSETS and DB_CHARSETS[0] else None

# --- Database Timeout Configuration ---
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", 10))  # seconds
DB_READ_TIMEOUT = int(os.getenv("DB_READ_TIMEOUT", 30))  # seconds
DB_WRITE_TIMEOUT = int(os.getenv("DB_WRITE_TIMEOUT", 30))  # seconds

# --- MCP Server Configuration ---
# Read-only mode
MCP_READ_ONLY = os.getenv("MCP_READ_ONLY", "true").lower() == "true"
MCP_MAX_POOL_SIZE = int(os.getenv("MCP_MAX_POOL_SIZE", 10))
MCP_MAX_RESULTS = int(os.getenv("MCP_MAX_RESULTS", 10000))  # Max rows returned per query

# --- Embedding Configuration ---
EMBEDDING_MAX_CONCURRENT = int(os.getenv("EMBEDDING_MAX_CONCURRENT", 5))  # Max concurrent embedding API calls

# --- Embedding Configuration ---
# Provider selection ('openai' or 'gemini' or 'huggingface')
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER")
EMBEDDING_PROVIDER = EMBEDDING_PROVIDER.lower() if EMBEDDING_PROVIDER else None
# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Open models from Huggingface
HF_MODEL = os.getenv("HF_MODEL")


# --- Validation ---
if not all([DB_USER, DB_PASSWORD]):
    logger.error("Database credentials (DB_USER, DB_PASSWORD) not found in environment variables or .env file.")

# Embedding Provider and Keys
logger.info(f"Selected Embedding Provider: {EMBEDDING_PROVIDER}")
if EMBEDDING_PROVIDER == "openai":
    if not OPENAI_API_KEY:
        logger.error("EMBEDDING_PROVIDER is 'openai' but OPENAI_API_KEY is missing.")
        raise ValueError("OpenAI API key is required when EMBEDDING_PROVIDER is 'openai'.")
elif EMBEDDING_PROVIDER == "gemini":
    if not GEMINI_API_KEY:
        logger.error("EMBEDDING_PROVIDER is 'gemini' but GEMINI_API_KEY is missing.")
        raise ValueError("Gemini API key is required when EMBEDDING_PROVIDER is 'gemini'.")
elif EMBEDDING_PROVIDER == "huggingface":
    if not HF_MODEL:
        logger.error("EMBEDDING_PROVIDER is 'huggingface' but HF_MODEL is missing.")
        raise ValueError("HuggingFace model is required when EMBEDDING_PROVIDER is 'huggingface'.")
else:
    EMBEDDING_PROVIDER = None
    logger.info(f"No EMBEDDING_PROVIDER selected or it is set to None. Disabling embedding features.")

logger.info(f"Read-only mode: {MCP_READ_ONLY}")
logger.info(f"Logging to console and to file: {LOG_FILE_PATH} (Level: {LOG_LEVEL}, MaxSize: {LOG_MAX_BYTES}B, Backups: {LOG_BACKUP_COUNT})")