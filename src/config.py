# config.py
import os
from dotenv import load_dotenv

# Import our dedicated logging configuration
from logging_config import setup_logger, get_logger, setup_third_party_logging

# Load environment variables from .env file
load_dotenv()

# --- Authentication Configuration ---
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "mariadb_ops_server")
JWT_ISSUER = os.getenv("JWT_ISSUER", "http://localhost")

# --- Logging Configuration ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = os.getenv("LOG_FILE", "logs/mcp_server.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 10 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))
THIRD_PARTY_LOG_LEVEL = os.getenv("THIRD_PARTY_LOG_LEVEL", "WARNING").upper()

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS")
if ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ALLOWED_ORIGINS.split(",")
else:
    ALLOWED_ORIGINS = ["http://localhost", "http://127.0.0.1", "http://*", "https://localhost", "https://127.0.0.1", "vscode-file://vscode-app"]

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS")
if ALLOWED_HOSTS:
    ALLOWED_HOSTS = ALLOWED_HOSTS.split(",")
else:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Set up the dedicated logger for this project (NOT the root logger)
logger = setup_logger(
    log_level=LOG_LEVEL,
    log_file_path=LOG_FILE_PATH,
    log_max_bytes=LOG_MAX_BYTES,
    log_backup_count=LOG_BACKUP_COUNT,
    enable_console=True,
    enable_file=True
)

# Configure third-party library logging to reduce noise
setup_third_party_logging(level=THIRD_PARTY_LOG_LEVEL)

# --- Database Configuration ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_CHARSET = os.getenv("DB_CHARSET")

# --- MCP Server Configuration ---
# Read-only mode
MCP_READ_ONLY = os.getenv("MCP_READ_ONLY", "true").lower() == "true"
MCP_MAX_POOL_SIZE = int(os.getenv("MCP_MAX_POOL_SIZE", 10))

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
