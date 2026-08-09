# backend/app/db/supabase_client.py
import os
from supabase import create_client, Client
import logging

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_supabase_client: Client | None = None

def get_supabase_client() -> Client | None:
    """
    Returns an initialized Supabase Client if SUPABASE_URL and SUPABASE_KEY environment variables are present.
    Returns None if environment variables are not configured.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if SUPABASE_URL and SUPABASE_KEY:
        try:
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Supabase client initialized successfully.")
            return _supabase_client
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            return None
    else:
        logger.info("SUPABASE_URL / SUPABASE_KEY not set. Using local database backend.")
        return None
