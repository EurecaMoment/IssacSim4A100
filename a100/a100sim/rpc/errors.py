"""Translate simulator failures to the public gRPC status vocabulary."""

from __future__ import annotations

import grpc


class ServiceError(Exception):
    """An expected, caller-visible failure with a canonical gRPC code."""

    def __init__(self, code: grpc.StatusCode, message: str) -> None:
        super().__init__(message)
        self.code = code
