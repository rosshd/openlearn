"""Opt-in, operator-configured transport ceiling for one live QA batch."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4


class QABudgetStop(Exception):
    """Stop before inference when the QA ceiling cannot be proven."""


def _stop(detail: str) -> QABudgetStop:
    return QABudgetStop(f"Live QA budget stopped: {detail}.")


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise _stop("invalid monetary configuration") from None
    if not result.is_finite() or result < 0:
        raise _stop("invalid monetary configuration")
    return result


def _integer(value: object, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _stop("invalid token or request bound")
    return value


def _json_price(value: object) -> float:
    price = _decimal(value)
    number = float(price)
    if Decimal(str(number)) != price:
        raise _stop("price cannot be represented safely in provider JSON")
    return number


@dataclass(frozen=True)
class QABudget:
    path: Path
    pricing: dict
    max_calls: int
    max_usd: Decimal
    lock: Callable
    write: Callable

    def request_options(self) -> dict:
        """Keep OpenRouter routing within the operator's verified price ceiling."""
        return {
            "reasoning": {"enabled": False, "exclude": True},
            "provider": {
                "allow_fallbacks": False,
                "require_parameters": True,
                "max_price": {
                    "prompt": _json_price(self.pricing["input_usd_per_million"]),
                    "completion": _json_price(self.pricing["output_usd_per_million"]),
                    "request": 0,
                },
            },
        }

    def _identity(self) -> dict:
        return {
            "schema": 1,
            "max_calls": self.max_calls,
            "max_usd": str(self.max_usd),
            "pricing_sha256": hashlib.sha256(
                json.dumps(self.pricing, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }

    def _read(self) -> dict:
        identity = self._identity()
        if not self.path.exists():
            return {**identity, "stopped": False, "attempts": []}
        try:
            ledger = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(ledger, dict) or any(
                ledger.get(key) != value for key, value in identity.items()
            ):
                raise _stop("existing batch configuration does not match")
            if type(ledger.get("stopped")) is not bool:
                raise _stop("invalid existing ledger")
            if ledger["stopped"]:
                raise _stop("existing batch has inconsistent provider usage")
            attempts = ledger["attempts"]
            if not isinstance(attempts, list) or len(attempts) > self.max_calls:
                raise _stop("invalid existing ledger")
            ids = set()
            for attempt in attempts:
                if (
                    not isinstance(attempt, dict)
                    or set(attempt) != {
                        "id", "reserved_usd", "charged_usd", "status",
                        "input_token_bound", "output_token_bound",
                    }
                    or not isinstance(attempt["id"], str)
                    or attempt["id"] in ids
                    or attempt["status"] not in {"reserved", "confirmed"}
                ):
                    raise _stop("invalid existing ledger")
                ids.add(attempt["id"])
                _integer(attempt["input_token_bound"])
                _integer(attempt["output_token_bound"])
                reserved = _decimal(attempt["reserved_usd"])
                charged = _decimal(attempt["charged_usd"])
                if reserved <= 0 or charged > reserved or (
                    attempt["status"] == "reserved" and charged != reserved
                ):
                    raise _stop("invalid existing ledger")
            if sum((_decimal(a["charged_usd"]) for a in attempts), Decimal(0)) > self.max_usd:
                raise _stop("existing ledger exceeds ceiling")
            return ledger
        except (OSError, ValueError, KeyError, TypeError):
            raise _stop("existing ledger cannot be read safely") from None

    def reserve(self, base_url: str, payload: Mapping) -> str:
        pricing = self.pricing
        if base_url != pricing["base_url"] or payload.get("model") != pricing["model"]:
            raise _stop("provider or model has no verified pricing")
        output_tokens = _integer(payload.get("max_tokens"))
        if output_tokens > pricing["max_output_tokens"]:
            raise _stop("output limit exceeds verified bound")
        if pricing.get("input_bound_mode", "utf8_bytes") == "context_window":
            input_tokens = pricing["context_tokens"]
        else:
            # Only use a byte estimate when its tokenizer/framing bounds were verified.
            input_tokens = len(
                json.dumps(payload["messages"], ensure_ascii=False).encode("utf-8")
            ) + pricing["input_framing_tokens"]
            if input_tokens + output_tokens > pricing["context_tokens"]:
                raise _stop("input exceeds verified context bound")
        amount = (
            input_tokens * _decimal(pricing["input_usd_per_million"])
            + output_tokens * _decimal(pricing["output_usd_per_million"])
        ) / Decimal(1_000_000)
        with self.lock(self.path):
            ledger = self._read()
            attempts = ledger["attempts"]
            used = sum((_decimal(a["charged_usd"]) for a in attempts), Decimal(0))
            if len(attempts) >= self.max_calls or used + amount > self.max_usd:
                raise _stop("batch request or dollar ceiling reached")
            attempt_id = uuid4().hex
            attempts.append({
                "id": attempt_id, "reserved_usd": str(amount),
                "charged_usd": str(amount), "status": "reserved",
                "input_token_bound": input_tokens, "output_token_bound": output_tokens,
            })
            self.write(self.path, json.dumps(ledger, sort_keys=True) + "\n")
        return attempt_id

    def settle(self, attempt_id: str, usage: object) -> None:
        # Missing, malformed, or ambiguous usage never releases a reservation.
        if not isinstance(usage, dict):
            return
        try:
            input_tokens = _integer(usage.get("prompt_tokens"), minimum=0)
            output_tokens = _integer(usage.get("completion_tokens"), minimum=0)
            total_tokens = _integer(usage.get("total_tokens"), minimum=0)
            if total_tokens != input_tokens + output_tokens:
                return
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict) and "reasoning_tokens" in details:
                reasoning = _integer(details["reasoning_tokens"], minimum=0)
                if reasoning > output_tokens:
                    return
            cost = (
                input_tokens * _decimal(self.pricing["input_usd_per_million"])
                + output_tokens * _decimal(self.pricing["output_usd_per_million"])
            ) / Decimal(1_000_000)
            reported_cost = _decimal(usage["cost"]) if "cost" in usage else cost
        except QABudgetStop:
            return
        with self.lock(self.path):
            ledger = self._read()
            attempt = next((a for a in ledger["attempts"] if a["id"] == attempt_id), None)
            if attempt is None or attempt["status"] != "reserved":
                raise _stop("reservation cannot be settled")
            if (
                cost > _decimal(attempt["reserved_usd"])
                or reported_cost > cost
                or input_tokens > attempt["input_token_bound"]
                or output_tokens > attempt["output_token_bound"]
            ):
                ledger["stopped"] = True
                self.write(self.path, json.dumps(ledger, sort_keys=True) + "\n")
                raise _stop("reported usage exceeds reservation")
            attempt.update(charged_usd=str(cost), status="confirmed")
            self.write(self.path, json.dumps(ledger, sort_keys=True) + "\n")


def from_environment(*, lock: Callable, write: Callable) -> QABudget | None:
    """Read only server-side opt-in settings; no client supplied pricing is accepted."""
    enabled = os.environ.get("OPENLEARN_QA_BUDGET", "0")
    if enabled == "0":
        return None
    if enabled != "1":
        raise _stop("opt-in must be explicitly 1")
    try:
        path = Path(os.environ["OPENLEARN_QA_LEDGER"])
        pricing_path = Path(os.environ["OPENLEARN_QA_PRICING"])
        max_calls = int(os.environ["OPENLEARN_QA_MAX_CALLS"])
        max_usd = _decimal(os.environ["OPENLEARN_QA_MAX_USD"])
        if (
            not path.is_absolute() or not pricing_path.is_absolute()
            or path.resolve() == pricing_path.resolve()
        ):
            raise _stop("separate absolute ledger and pricing paths are required")
        if not 1 <= max_calls <= 12 or not 0 < max_usd <= Decimal("0.05"):
            raise _stop("ceilings must be at most 12 requests and USD 0.05")
        pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
        verified_at = datetime.fromisoformat(pricing["verified_at"])
        now = datetime.now(timezone.utc)
        if verified_at.tzinfo is None or not timedelta(0) <= now - verified_at <= timedelta(days=1):
            raise _stop("pricing snapshot is stale or undated")
        if (
            pricing["base_url"] != "https://openrouter.ai/api/v1"
            or pricing["model"] != "deepseek/deepseek-v4.1-flash"
            or pricing["pricing_source"] != "https://openrouter.ai/api/v1/models"
            or not pricing["bounds_source"].startswith("https://openrouter.ai/docs/")
            or pricing["max_tokens_includes_reasoning"] is not True
            or pricing["reasoning_disabled_supported"] is not True
            or _decimal(pricing["request_usd"]) != 0
        ):
            raise _stop("pricing or charged token bounds are unsupported")
        for key in ("context_tokens", "max_output_tokens"):
            _integer(pricing[key])
        mode = pricing.get("input_bound_mode", "utf8_bytes")
        if mode == "utf8_bytes":
            _integer(pricing["input_framing_tokens"])
            if _integer(pricing["input_tokens_per_utf8_byte"]) != 1:
                raise _stop("input token bound is unsupported")
        elif mode != "context_window":
            raise _stop("input token bound is unsupported")
        for key in ("input_usd_per_million", "output_usd_per_million"):
            if _decimal(pricing[key]) <= 0:
                raise _stop("verified positive token prices are required")
            _json_price(pricing[key])
        return QABudget(path, pricing, max_calls, max_usd, lock, write)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise _stop("explicit trusted pricing and batch configuration are required") from None
