import random
import time
from typing import Dict, Union

import anthropic
import openai
import tiktoken


def num_tokens_from_messages(message, model="gpt-3.5-turbo-0301"):
    """Returns the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    if isinstance(message, list):
        # use last message.
        num_tokens = len(encoding.encode(message[0]["content"]))
    else:
        num_tokens = len(encoding.encode(message))
    return num_tokens


def create_chatgpt_config(
    message: Union[str, list],
    max_tokens: int,
    temperature: float = 1,
    batch_size: int = 1,
    system_message: str = "You are a helpful assistant.",
    model: str = "gpt-3.5-turbo",
) -> Dict:
    if isinstance(message, list):
        config = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "n": batch_size,
            "messages": [{"role": "system", "content": system_message}] + message,
        }
    else:
        config = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "n": batch_size,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": message},
            ],
        }
    return config


def handler(signum, frame):
    # swallow signum and frame
    raise Exception("end of time")


def request_chatgpt_engine(config, logger, base_url=None, max_retries=40, timeout=100):
    """Request an OpenAI-compatible chat completion with bounded retries.

    ``timeout`` is passed to the OpenAI client so a hung request cannot block
    forever.  Retryable errors use exponential backoff with jitter.
    Authentication and permission errors are raised immediately.
    """
    ret = None
    client = openai.OpenAI(base_url=base_url, timeout=timeout)

    for retries in range(max_retries):
        try:
            logger.info("Creating API request")
            ret = client.chat.completions.create(**config)
            break
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            logger.error("Authentication or permission error: %s", e)
            raise
        except openai.BadRequestError as e:
            logger.error("Invalid API request: %s", e)
            raise
        except (openai.RateLimitError, openai.APIConnectionError) as e:
            delay = min(60.0, 2 ** min(retries, 6)) + random.uniform(0, 1)
            logger.warning(
                "Transient OpenAI error (%s); retrying in %.1fs (%d/%d)",
                type(e).__name__,
                delay,
                retries + 1,
                max_retries,
            )
            time.sleep(delay)
        except openai.OpenAIError as e:
            logger.error("Non-retryable OpenAI error: %s", e)
            raise

    if ret is None:
        raise RuntimeError(f"OpenAI request failed after {max_retries} attempts")
    logger.info("API response received")
    return ret


def create_anthropic_config(
    message: str,
    max_tokens: int,
    temperature: float = 1,
    batch_size: int = 1,
    system_message: str = "You are a helpful assistant.",
    model: str = "claude-2.1",
    tools: list = None,
) -> Dict:
    if isinstance(message, list):
        config = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": message,
        }
    else:
        config = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": message}]},
            ],
        }

    if tools:
        config["tools"] = tools

    return config


def request_anthropic_engine(
    config, logger, max_retries=40, timeout=500, prompt_cache=False
):
    """Request an Anthropic message with a real total timeout and backoff."""
    ret = None
    client = anthropic.Anthropic(timeout=timeout)
    start_time = time.time()

    for retries in range(max_retries):
        try:
            if time.time() - start_time >= timeout:
                raise TimeoutError(
                    f"Anthropic request exceeded total timeout of {timeout}s"
                )
            if prompt_cache:
                # following best practice to cache mainly the reused content at the beginning
                # this includes any tools, system messages (which is already handled since we try to cache the first message)
                config["messages"][0]["content"][0]["cache_control"] = {
                    "type": "ephemeral"
                }
                ret = client.beta.prompt_caching.messages.create(**config)
            else:
                ret = client.messages.create(**config)
            break
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            logger.error("Authentication or permission error: %s", e)
            raise
        except Exception as e:
            if time.time() - start_time >= timeout:
                logger.error("Anthropic request timed out after %ss", timeout)
                raise TimeoutError(
                    f"Anthropic request exceeded total timeout of {timeout}s"
                ) from e
            delay = min(30.0, 2 ** min(retries, 5)) + random.uniform(0, 1)
            logger.warning(
                "Anthropic request error (%s); retrying in %.1fs (%d/%d)",
                type(e).__name__,
                delay,
                retries + 1,
                max_retries,
            )
            time.sleep(delay)

    if ret is None:
        raise RuntimeError(f"Anthropic request failed after {max_retries} attempts")
    return ret
