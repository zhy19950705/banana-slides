"""
Client identity utilities.

This project uses per-client data partitioning by requiring a stable client id
from request header `X-Client-Id`.
"""
import re
from typing import Optional, Tuple

from flask import request

from .response import error_response

CLIENT_ID_HEADER = "X-Client-Id"
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def get_client_id_or_error() -> Tuple[Optional[str], Optional[tuple]]:
    """
    Read and validate client id from request headers.

    Returns:
        (client_id, None) when valid
        (None, flask_response) when invalid/missing
    """
    raw_value = request.headers.get(CLIENT_ID_HEADER, "")
    client_id = raw_value.strip()

    if not client_id:
        return None, error_response(
            "CLIENT_ID_REQUIRED",
            f"Missing required header: {CLIENT_ID_HEADER}",
            400,
        )

    if not _CLIENT_ID_PATTERN.match(client_id):
        return None, error_response(
            "INVALID_CLIENT_ID",
            "Invalid client id format",
            400,
        )

    return client_id, None
