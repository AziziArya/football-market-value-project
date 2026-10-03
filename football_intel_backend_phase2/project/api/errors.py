"""Error type mapped to RFC 9457 problem details by the app's exception handlers (contract: Problem)."""
from __future__ import annotations

from api.domain.enums import ProblemCode

_TITLES = {
    ProblemCode.INVALID_PARAMETER: "Invalid parameter",
    ProblemCode.INVALID_PLAYER_ID: "Invalid player id",
    ProblemCode.PLAYER_NOT_FOUND: "Player not found",
    ProblemCode.DATA_UNAVAILABLE: "Data unavailable",
    ProblemCode.INTERNAL_ERROR: "Internal error",
}
_STATUS = {
    ProblemCode.INVALID_PARAMETER: 400,
    ProblemCode.INVALID_PLAYER_ID: 400,
    ProblemCode.PLAYER_NOT_FOUND: 404,
    ProblemCode.DATA_UNAVAILABLE: 503,
    ProblemCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    """`detail` is human-readable and safe to show: never SQL, file paths or stack traces."""

    def __init__(self, code: ProblemCode, detail: str | None = None, errors: list[dict] | None = None, redact_instance: bool = False):
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.status = _STATUS[code]
        self.title = _TITLES[code]
        self.detail = detail
        self.errors = errors
        self.redact_instance = redact_instance      # True when the request path itself contains the rejected value


def data_unavailable() -> ApiError:
    return ApiError(ProblemCode.DATA_UNAVAILABLE, "The data service is temporarily unavailable.")


def invalid_parameters(errors: list[dict]) -> ApiError:
    """400 INVALID_PARAMETER; `errors` = [{"parameter": ..., "reason": ...}]."""
    return ApiError(ProblemCode.INVALID_PARAMETER, "One or more parameters are invalid.", errors)


def invalid_player_id() -> ApiError:
    """400 INVALID_PLAYER_ID. The offending value is deliberately NOT echoed back."""
    return ApiError(ProblemCode.INVALID_PLAYER_ID, "The player id must look like ea:<number>, e.g. ea:239085.",
                    [{"parameter": "player_id", "reason": "must match ea:<number> (no leading zeros, at most 9 digits)"}],
                    redact_instance=True)


def player_not_found(player_id: str) -> ApiError:
    """404 only for a WELL-FORMED id (so echoing it is safe) that no EA player has."""
    return ApiError(ProblemCode.PLAYER_NOT_FOUND, f"No player with id {player_id}.")
