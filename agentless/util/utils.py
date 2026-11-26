import json
import logging
import os
from collections import defaultdict


def load_jsonl(filepath):
    """
    Load a JSONL file from the given filepath.

    Arguments:
    filepath -- the path to the JSONL file to load

    Returns:
    A list of dictionaries representing the data in each line of the JSONL file.
    """
    with open(filepath, "r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(data, filepath):
    """
    Write data to a JSONL file at the given filepath.

    Arguments:
    data -- a list of dictionaries to write to the JSONL file
    filepath -- the path to the JSONL file to write
    """
    with open(filepath, "w", encoding="utf-8") as file:
        for entry in data:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8") as file:
        return json.load(file)


def combine_by_instance_id(data):
    """
    Combine data entries by their instance ID.

    Arguments:
    data -- a list of dictionaries with instance IDs and other information

    Returns:
    A list of combined dictionaries by instance ID with all associated data.
    """
    combined_data = defaultdict(lambda: defaultdict(list))
    for item in data:
        instance_id = item.get("instance_id")
        if not instance_id:
            continue
        for key, value in item.items():
            if key != "instance_id":
                combined_data[instance_id][key].extend(
                    value if isinstance(value, list) else [value]
                )
    return [
        {**{"instance_id": iid}, **details} for iid, details in combined_data.items()
    ]


def setup_logger(log_file):
    logger = logging.getLogger(log_file)
    logger.setLevel(logging.DEBUG)

    # Avoid attaching duplicate handlers when a pipeline step reuses a logger.
    absolute_log_file = os.path.abspath(log_file)
    if any(
        isinstance(handler, logging.FileHandler)
        and os.path.abspath(handler.baseFilename) == absolute_log_file
        for handler in logger.handlers
    ):
        return logger

    log_dir = os.path.dirname(absolute_log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)

    logger.addHandler(fh)
    return logger


def cleanup_logger(logger):
    handlers = logger.handlers[:]
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()


def load_existing_instance_ids(output_file):
    instance_ids = set()
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    instance_ids.add(data["instance_id"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    return instance_ids
