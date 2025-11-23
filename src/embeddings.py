import sys
import os
import asyncio
from typing import List, Optional, Dict, Any, Union, Awaitable
import numpy as np

# Import configuration variables
from config import (
    EMBEDDING_PROVIDER,
    OPENAI_API_KEY,
    GEMINI_API_KEY,
    HF_MODEL
)

# Import the dedicated logger
from logging_config import get_logger

logger = get_logger("embeddings")

# Import specific client libraries
try:
    from openai import AsyncOpenAI, OpenAIError
except ImportError:
    logger.warning("OpenAI library not installed. OpenAI provider will not be available.")
    AsyncOpenAI = None # type: ignore
    OpenAIError = Exception # type: ignore # Generic exception if library missing

# Ensure site-packages is in path for google-genai
site_packages_paths = [
    os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages'),
    os.path.join(os.path.dirname(os.path.dirname(sys.executable)), 'Lib', 'site-packages')
]
for path in site_packages_paths:
    if path not in sys.path and os.path.exists(path):
        logger.info(f"Adding path to sys.path: {path}")
        sys.path.append(path)

# Import Google Genai SDK
try:
    import google.genai as genai
    from google.api_core import exceptions as GoogleAPICoreExceptions
    logger.info("Successfully imported google.genai")
except ImportError as e:
    logger.warning(f"Google Generative AI SDK ('google-genai' package) not installed. Gemini provider will not be available. Error: {e}")
    logger.info(f"Current sys.path: {sys.path}")
    genai = None # type: ignore
    GoogleAPICoreExceptions = None # type: ignore

# --- Model Definitions ---
# Define allowed models and defaults for each provider
# OpenAI Embedding Models
ALLOWED_OPENAI_MODELS: List[str] = ["text-embedding-3-small", "text-embedding-3-large"]
DEFAULT_OPENAI_MODEL: str = "text-embedding-3-small"
# Mapping of model names to their embedding dimensions (update as needed)
OPENAI_MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072
}
# Gemini Embedding Models
ALLOWED_GEMINI_MODELS: List[str] = ["text-embedding-004"]
DEFAULT_GEMINI_MODEL: str = "text-embedding-004"
GEMINI_MODEL_DIMENSIONS = {
    "text-embedding-004": 768
}
# Open Embedding Models - Huggingface
ALLOWED_HF_MODELS: List[str] = ["intfloat/multilingual-e5-large-instruct", "BAAI/bge-m3"]
DEFAULT_HF_MODEL: str = "BAAI/bge-m3"
HF_MODEL_DIMENSIONS = {
    "intfloat/multilingual-e5-large-instruct": 1024,
    "BAAI/bge-m3": 1024
}

class EmbeddingService:
    """
    Provides an interface to generate text embeddings using a configured provider
    (OpenAI or Google Gemini) and allows model selection at runtime.
    """
    def __init__(self):
        """
        Initializes the embedding service based on configuration.
        Sets up the appropriate asynchronous client for OpenAI or configures Gemini.
        """
        self.provider = EMBEDDING_PROVIDER
        self.openai_client: Optional[AsyncOpenAI] = None
        self.gemini_client = None
        self.allowed_models: List[str] = []
        self.default_model: str = ""

        logger.info(f"Initializing EmbeddingService with provider: {self.provider}")

        if self.provider == "openai":
            if not AsyncOpenAI:
                logger.error("OpenAI provider selected, but 'openai' library is not installed.")
                raise ImportError("OpenAI library not found. Please install it.")
            if not OPENAI_API_KEY:
                logger.error("OpenAI API key is missing.")
                raise ValueError("OpenAI API key is required for the OpenAI provider.")
            try:
                self.openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
                self.allowed_models = ALLOWED_OPENAI_MODELS
                self.default_model = DEFAULT_OPENAI_MODEL
                logger.info(f"OpenAI client initialized. Default model: {self.default_model}. Allowed: {self.allowed_models}")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
                raise RuntimeError(f"OpenAI client initialization failed: {e}")
        elif self.provider == "gemini":
            if not GEMINI_API_KEY:
                logger.error("Gemini API key is missing.")
                raise ValueError("Gemini API key is required for the Gemini provider.")
            try:
                import google.genai as genai
                from google.genai import types as genai_types
                self.gemini_client = genai.Client(api_key=GEMINI_API_KEY) # Keeping self.gemini_client = genai based on previous structure for embed_content
                self.gemini_config = genai_types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
                self.allowed_models = ALLOWED_GEMINI_MODELS
                self.default_model = DEFAULT_GEMINI_MODEL
                logger.info(f"Gemini client initialized. Default model: {self.default_model}. Allowed: {self.allowed_models}")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
                raise RuntimeError(f"Gemini client initialization failed: {e}")
        elif self.provider == "huggingface":
            if not HF_MODEL: # From config.py
                logger.error("EMBEDDING_PROVIDER is 'huggingface' but HF_MODEL is missing in config.")
                raise ValueError("HuggingFace model (HF_MODEL) is required in config for the HuggingFace provider.")
            try:
                from sentence_transformers import SentenceTransformer
                
                # The primary model for this service instance will be HF_MODEL from config
                self.default_model = HF_MODEL 
                self.allowed_models = ALLOWED_HF_MODELS # These are other models that can be specified via embed()
                
                # Pre-load the default model from config
                logger.info(f"Initializing SentenceTransformer with configured HF_MODEL: {self.default_model}")
                self.huggingface_client = SentenceTransformer(self.default_model) 
                # self.huggingface_client now holds the loaded model instance for config.HF_MODEL

                logger.info(f"HuggingFace provider initialized. Default model (from config.HF_MODEL): '{self.default_model}'. Client loaded. Allowed models for override: {self.allowed_models}")

            except ImportError:
                logger.error("'sentence-transformers' library not installed. HuggingFace provider will not be available.")
                self.huggingface_client = None # Ensure it's None if import fails
                raise ImportError("'sentence-transformers' library not found. Please install it.")
            except Exception as e:
                logger.error(f"Failed to initialize HuggingFace SentenceTransformer with model '{HF_MODEL}': {e}", exc_info=True)
                self.huggingface_client = None # Ensure it's None if init fails
                raise RuntimeError(f"HuggingFace SentenceTransformer (model: {HF_MODEL}) initialization failed: {e}")
        else:
            logger.error(f"Unsupported embedding provider configured: {self.provider}")
            raise ValueError(f"Unsupported embedding provider: {self.provider}")

    def get_allowed_models(self) -> List[str]:
        """Returns the list of allowed model names for the current provider."""
        return self.allowed_models

    def get_default_model(self) -> str:
        """Returns the default model name for the current provider."""
        return self.default_model

    async def get_embedding_dimension(self, model_name: Optional[str] = None) -> int:
        """
        Returns the embedding vector dimension for the given model (or default model if not specified).
        Raises ValueError if the model is invalid or dimension unknown.
        """
        # If in the future you want to fetch dimensions from an API, this can be awaited
        if self.provider == "openai":
            model = model_name or self.default_model
            if model not in OPENAI_MODEL_DIMENSIONS:
                logger.error(f"Unknown dimension for OpenAI model '{model}'. Known: {list(OPENAI_MODEL_DIMENSIONS.keys())}")
                raise ValueError(f"Unknown dimension for OpenAI model '{model}'.")
            return OPENAI_MODEL_DIMENSIONS[model]
        elif self.provider == "gemini":
            model = model_name or self.default_model
            if model not in GEMINI_MODEL_DIMENSIONS:
                logger.error(f"Unknown dimension for Gemini model '{model}'. Known: {list(GEMINI_MODEL_DIMENSIONS.keys())}")
                raise ValueError(f"Unknown dimension for Gemini model '{model}'.")
            return GEMINI_MODEL_DIMENSIONS[model]
        elif self.provider == "huggingface":
            model_to_check = model_name or self.default_model # self.default_model is config.HF_MODEL

            # If it's the default (pre-loaded) model, get dimension from the client
            if model_to_check == self.default_model and self.huggingface_client:
                try:
                    dimension = self.huggingface_client.get_sentence_embedding_dimension()
                    if dimension is None: # Fallback for some models
                        logger.warning(f"get_sentence_embedding_dimension() returned None for '{model_to_check}'. Attempting dummy embed to get dimension.")
                        # Note: encode() might return a list of embeddings if input is a list.
                        # We need to ensure we get a single embedding's dimension.
                        dummy_embeddings_np = self.huggingface_client.encode("test") # encode a single string
                        # Result of encode for single string might be 1D array or 2D array with 1 row
                        if isinstance(dummy_embeddings_np, np.ndarray) and dummy_embeddings_np.ndim == 1:
                            dimension = len(dummy_embeddings_np)
                        elif isinstance(dummy_embeddings_np, np.ndarray) and dummy_embeddings_np.ndim == 2 and dummy_embeddings_np.shape[0] == 1:
                            dimension = dummy_embeddings_np.shape[1]
                        else:
                            raise ValueError("Unexpected dummy embedding format for dimension check.")
                    logger.info(f"Dimension for default HF model '{model_to_check}' from client: {dimension}")
                    return dimension
                except Exception as e:
                    logger.error(f"Error getting dimension from loaded HF client for '{model_to_check}': {e}. Falling back to HF_MODEL_DIMENSIONS.")
            
            # Fallback or for other models specified by model_name, check the predefined dictionary
            if model_to_check in HF_MODEL_DIMENSIONS:
                logger.debug(f"Using HF_MODEL_DIMENSIONS for HuggingFace model '{model_to_check}'.")
                return HF_MODEL_DIMENSIONS[model_to_check]
            else:
                logger.error(f"Unknown dimension for HuggingFace model '{model_to_check}'. Not in HF_MODEL_DIMENSIONS and not derivable from a non-default model without loading it.")
                raise ValueError(f"Unknown dimension for HuggingFace model '{model_to_check}'. Please ensure it's in HF_MODEL_DIMENSIONS if not the default model.")
        else:
            logger.error(f"get_embedding_dimension not implemented for provider: {self.provider}")
            raise NotImplementedError(f"Embedding dimension lookup not implemented for provider: {self.provider}")

    async def embed(self, text: Union[str, List[str]], model_name: Optional[str] = None) -> Union[List[float], List[List[float]]]:
        """
        Asynchronously generates embedding(s) for a single document or a list of documents using the configured provider.

        Parameters:
        - text (str or List[str]): The text(s) to embed.
        - model_name (str, optional): The specific model to use. If None, uses the provider's default model.

        Returns:
        - List[float]: The generated embedding vector (if input is str).
        - List[List[float]]: The generated embedding vectors (if input is List[str]).

        Raises:
        - ValueError: If an invalid model_name is provided or input is empty/invalid.
        - RuntimeError: If the embedding API call fails for other reasons.
        """

        # Validate input
        if isinstance(text, str):
            if not text:
                logger.error("Embedding requested for empty string, which is not allowed.")
                raise ValueError("Cannot generate embedding for empty text.")
            texts = [text]
            single_input = True
        elif isinstance(text, list):
            if not text:
                logger.error("Embedding requested for empty list, which is not allowed.")
                raise ValueError("Cannot generate embedding for empty list.")
            if not all(isinstance(t, str) and t for t in text):
                logger.error("Embedding requested for a list containing non-string or empty elements.")
                raise ValueError("All elements in the input list must be non-empty strings.")
            texts = text
            single_input = False
        else:
            logger.error(f"Embedding requested for unsupported input type: {type(text)}")
            raise ValueError("Input must be a string or a list of strings.")

        target_model = model_name
        if target_model:
            if target_model not in self.allowed_models:
                logger.error(f"Invalid model '{target_model}' requested for provider '{self.provider}'. Allowed: {self.allowed_models}")
                raise ValueError(f"Model '{target_model}' is not allowed for the '{self.provider}' provider. Choose from: {self.allowed_models}")
        else:
            target_model = self.default_model
            logger.debug(f"No model specified, using default for {self.provider}: {target_model}")

        logger.debug(f"Requesting embedding using model '{target_model}' for {len(texts)} text(s). Example (first 50 chars): '{texts[0][:50]}...'")

        try:
            if self.provider == "openai":
                if not self.openai_client:
                    logger.critical("OpenAI client not initialized during embed call.")
                    raise RuntimeError("OpenAI client not initialized.")
                
                response = await self.openai_client.embeddings.create(
                    input=texts,
                    model=target_model
                )
                if response.data and len(response.data) == len(texts):
                    embeddings = [d.embedding for d in response.data]
                    logger.debug(f"OpenAI embedding(s) received. Count: {len(embeddings)}, Dimension: {len(embeddings[0]) if embeddings else 'N/A'}")
                    return embeddings[0] if single_input else embeddings
                else:
                    logger.error("OpenAI embedding API response did not contain expected data or count mismatch.")
                    raise RuntimeError("Invalid response structure from OpenAI embedding API.")
            elif self.provider == "gemini":
                if not self.gemini_client:
                    logger.critical("Gemini client not initialized during embed call.")
                    raise RuntimeError("Gemini client not initialized.")
                
                # Since Gemini doesn't have an async API yet, we'll use asyncio.to_thread
                embeddings = []
                for t in texts:
                    embedding_result = await asyncio.to_thread(
                        self.gemini_client.models.embed_content,
                        model=f'models/{target_model}', # Gemini models often need 'models/' prefix
                        contents=t,
                        config=self.gemini_config
                    )
                    # For the newer Gemini API, the embedding should be directly accessible
                    # through the embedding attribute of the response
                    if hasattr(embedding_result, 'embedding') and isinstance(embedding_result.embedding, list):
                        embeddings.append(embedding_result.embedding)
                    # Fallback for other response structures
                    elif isinstance(embedding_result, dict) and 'embedding' in embedding_result:
                        embeddings.append(embedding_result['embedding'])
                    elif hasattr(embedding_result, 'embeddings') and embedding_result.embeddings and hasattr(embedding_result.embeddings[0], 'values'):
                        embeddings.append(embedding_result.embeddings[0].values)
                    else:
                        logger.error(f"Unexpected Gemini embedding result structure: {type(embedding_result).__name__}")
                        logger.error(f"Available attributes: {dir(embedding_result) if hasattr(embedding_result, '__dir__') else 'Not inspectable'}")
                        raise RuntimeError("Failed to parse Gemini embedding result.")
            
                logger.debug(f"Gemini embedding(s) received. Count: {len(embeddings)}, Dimension: {len(embeddings[0]) if embeddings else 'N/A'}")
                
                return embeddings[0] if single_input else embeddings
            elif self.provider == "huggingface":
                if not self.huggingface_client: # This client is now pre-loaded with config.HF_MODEL
                    logger.critical("HuggingFace client (SentenceTransformer) not properly initialized.")
                    raise RuntimeError("HuggingFace client (SentenceTransformer) not initialized. Check service setup.")

                # target_model is already determined: model_name if valid, else self.default_model (which is config.HF_MODEL)
                
                embeddings_np: np.ndarray
                effective_model_name = target_model

                if target_model == self.default_model:
                    logger.debug(f"Using pre-loaded HuggingFace model '{self.default_model}' for embedding.")
                    embeddings_np = self.huggingface_client.encode(texts)
                else:
                    # A different model was requested via model_name, and it's valid (already checked in pre-amble of embed)
                    logger.info(f"Dynamically loading HuggingFace model '{target_model}' for this embed call (different from pre-loaded '{self.default_model}').")
                    try:
                        # Ensure sentence_transformers is available for dynamic loading too
                        from sentence_transformers import SentenceTransformer 
                        dynamic_model_loader = SentenceTransformer(target_model)
                        embeddings_np = dynamic_model_loader.encode(texts)
                    except Exception as e:
                        logger.error(f"Failed to load or use dynamically specified HuggingFace model '{target_model}': {e}", exc_info=True)
                        raise RuntimeError(f"Error with HuggingFace model '{target_model}': {e}")
            
                # Convert numpy array to list of lists of floats (or list of floats)
                embeddings_list: Union[List[float], List[List[float]]]
                if isinstance(embeddings_np, np.ndarray):
                    embeddings_list = embeddings_np.tolist()
                else: # Should ideally not happen with sentence-transformers if encode ran
                    logger.warning("HuggingFace encode did not return a numpy array as expected.")
                    embeddings_list = texts # Fallback, though likely incorrect

                logger.debug(f"HuggingFace embedding(s) with model '{effective_model_name}' received. Count: {len(embeddings_list)}, Dimension: {len(embeddings_list[0]) if embeddings_list and isinstance(embeddings_list[0], list) and embeddings_list[0] else (len(embeddings_list) if embeddings_list and not isinstance(embeddings_list[0], list) else 'N/A')}")
                
                # Adjust return for single_input
                if single_input:
                    return embeddings_list[0] if embeddings_list and isinstance(embeddings_list, list) and embeddings_list[0] else embeddings_list
                else:
                    return embeddings_list
            else:
                logger.error(f"Embed called with unsupported provider: {self.provider}")
                raise RuntimeError(f"Unsupported embedding provider: {self.provider}")
            
        except OpenAIError as e:
            logger.error(f"OpenAI API error during embedding: {e}", exc_info=True)
            raise RuntimeError(f"OpenAI API error: {e}") from e
        except GoogleAPICoreExceptions.GoogleAPIError as e: # type: ignore
            logger.error(f"Gemini API error during embedding: {e}", exc_info=True)
            raise RuntimeError(f"Gemini API error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during embedding with {self.provider} model {target_model}: {e}", exc_info=True)
            raise RuntimeError(f"Embedding generation failed: {e}")
