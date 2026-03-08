"""
LLM Utility — Shared helpers for all PetCare agents.

Authors: Syed Ali Turab | Team: Broadview
Date:   March 8, 2026

Provides:
  - llm_call_with_retry: Thin wrapper around client.chat.completions.create
    that retries on transient OpenAI errors with exponential backoff.
    Covers rate-limit (429), server errors (500/502/503), and timeouts.
"""

import time
import logging
import openai

logger = logging.getLogger('petcare.utils.llm')

# Errors that are safe to retry (transient)
_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


def llm_call_with_retry(
    client,
    *,
    model: str,
    messages: list,
    max_tokens: int,
    temperature: float = 0.3,
    max_retries: int = 3,
    base_delay: float = 1.5,
) -> str:
    """
    Call client.chat.completions.create with exponential-backoff retry.

    Args:
        client:       An openai.OpenAI (or LangSmith-wrapped) client instance.
        model:        Model ID string (e.g. 'gpt-4o-mini').
        messages:     Chat messages list.
        max_tokens:   Token limit for the response.
        temperature:  Sampling temperature.
        max_retries:  Maximum number of retry attempts (default 3).
        base_delay:   Base delay in seconds; doubles each attempt (default 1.5s).

    Returns:
        The raw response text from the model (stripped).

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            return resp.choices[0].message.content.strip()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"LLM transient error (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {delay:.1f}s: {exc}"
                )
                time.sleep(delay)
            else:
                logger.error(f"LLM call failed after {max_retries + 1} attempts: {exc}")
        except Exception as exc:
            # Non-retryable error — raise immediately
            raise exc
    raise last_exc
