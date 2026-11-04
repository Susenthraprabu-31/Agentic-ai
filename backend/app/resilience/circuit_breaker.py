"""Distributed and persistent Circuit Breaker system for external dependencies."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.circuit_breaker import CircuitBreakerRecord, CircuitState
from app.resilience.error_classifier import ErrorClassifier
from app.resilience.exceptions import CircuitBreakerOpenException

logger = get_logger(__name__)


class CircuitBreaker:
    """
    Manages circuit state (CLOSED, OPEN, HALF_OPEN) across external dependencies.
    Uses Redis for low-latency shared distributed state with automatic fallback
    to PostgreSQL / in-memory when Redis is unavailable.
    """

    def __init__(self) -> None:
        self._local_cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._redis_client: Any | None = None
        self._redis_loop: Any | None = None
        self._redis_disabled: bool = False

    def _get_defaults(self) -> dict[str, Any]:
        return {
            "failure_threshold": settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            "window_seconds": settings.CIRCUIT_BREAKER_WINDOW_SECONDS,
            "open_duration_seconds": settings.CIRCUIT_BREAKER_OPEN_SECONDS,
            "half_open_max_probes": settings.CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS,
            "success_threshold": settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
        }

    async def _get_redis_client(self) -> Any | None:
        """Returns cached async Redis client or None if unreachable."""
        if self._redis_disabled:
            return None
        try:
            loop = asyncio.get_running_loop()
            if self._redis_client is not None and self._redis_loop == loop:
                return self._redis_client

            import redis.asyncio as aioredis
            client = aioredis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                decode_responses=True,
            )
            await client.ping()
            self._redis_client = client
            self._redis_loop = loop
            return client
        except Exception:
            self._redis_disabled = True
            return None

    async def can_execute(
        self,
        dependency_key: str,
        dependency_type: str = "general",
    ) -> tuple[bool, str | None]:
        """
        Determines if a request to the dependency is allowed.
        Returns: (is_allowed: bool, rejection_reason: str | None)
        """
        async with self._lock:
            state_data = await self._load_state(dependency_key, dependency_type)
            now = datetime.now(timezone.utc)
            state = state_data["state"]

            if state == CircuitState.CLOSED.value:
                return True, None

            if state == CircuitState.OPEN.value:
                open_until = state_data.get("open_until")
                if open_until:
                    if isinstance(open_until, str):
                        open_until = datetime.fromisoformat(open_until)
                    if open_until.tzinfo is None:
                        open_until = open_until.replace(tzinfo=timezone.utc)

                if open_until and now >= open_until:
                    # Transition to HALF_OPEN
                    logger.info("Circuit breaker for '%s' cooldown expired. Transitioning to HALF_OPEN.", dependency_key)
                    state_data["state"] = CircuitState.HALF_OPEN.value
                    state_data["half_open_active_probes"] = 1
                    state_data["consecutive_successes"] = 0
                    state_data["last_state_change_at"] = now.isoformat()
                    await self._save_state(dependency_key, state_data)
                    return True, None
                else:
                    until_str = open_until.isoformat() if open_until else "reset"
                    return False, f"Circuit breaker for '{dependency_key}' is OPEN until {until_str}"

            if state == CircuitState.HALF_OPEN.value:
                active_probes = state_data.get("half_open_active_probes", 0)
                max_probes = state_data.get("half_open_max_probes", settings.CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS)
                if active_probes < max_probes:
                    state_data["half_open_active_probes"] = active_probes + 1
                    await self._save_state(dependency_key, state_data)
                    return True, None
                else:
                    return False, f"Circuit breaker for '{dependency_key}' is HALF_OPEN and max probe limit ({max_probes}) reached"

            return True, None

    async def record_success(self, dependency_key: str, dependency_type: str = "general") -> None:
        """Records a successful operation against the dependency."""
        async with self._lock:
            state_data = await self._load_state(dependency_key, dependency_type)
            now = datetime.now(timezone.utc)
            state = state_data["state"]

            state_data["last_success_at"] = now.isoformat()
            state_data["success_count"] = state_data.get("success_count", 0) + 1

            if state == CircuitState.HALF_OPEN.value:
                consecutive = state_data.get("consecutive_successes", 0) + 1
                state_data["consecutive_successes"] = consecutive
                success_threshold = state_data.get("success_threshold", settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD)

                if consecutive >= success_threshold:
                    logger.info(
                        "Circuit breaker for '%s' probe successes (%d) met threshold. Transitioning to CLOSED.",
                        dependency_key,
                        consecutive,
                    )
                    state_data["state"] = CircuitState.CLOSED.value
                    state_data["failure_count"] = 0
                    state_data["consecutive_successes"] = 0
                    state_data["half_open_active_probes"] = 0
                    state_data["open_until"] = None
                    state_data["last_state_change_at"] = now.isoformat()
            elif state == CircuitState.CLOSED.value:
                # Reset failure counter on verified healthy execution
                state_data["failure_count"] = 0

            await self._save_state(dependency_key, state_data)

    async def record_failure(
        self,
        dependency_key: str,
        error: Any,
        dependency_type: str = "general",
        reason: str | None = None,
    ) -> None:
        """
        Records a failure against the dependency if classified as a transient infrastructure failure.
        Security challenges and authorization failures are deliberately ignored.
        """
        if not ErrorClassifier.is_circuit_breaker_failure(error):
            logger.debug(
                "Error '%s' on '%s' is not classified as a circuit-breaker failure. Ignoring.",
                error,
                dependency_key,
            )
            return

        async with self._lock:
            state_data = await self._load_state(dependency_key, dependency_type)
            now = datetime.now(timezone.utc)
            state = state_data["state"]
            open_duration = state_data.get("open_duration_seconds", settings.CIRCUIT_BREAKER_OPEN_SECONDS)

            state_data["last_failure_at"] = now.isoformat()
            state_data["last_failure_reason"] = reason or str(error)

            if state == CircuitState.HALF_OPEN.value:
                # Immediate trip back to OPEN
                logger.warning(
                    "Circuit breaker probe failed for '%s' with error: %s. Reopening circuit for %ds.",
                    dependency_key,
                    error,
                    open_duration,
                )
                state_data["state"] = CircuitState.OPEN.value
                state_data["open_until"] = (now + timedelta(seconds=open_duration)).isoformat()
                state_data["last_state_change_at"] = now.isoformat()
                state_data["half_open_active_probes"] = 0
                state_data["consecutive_successes"] = 0
            elif state == CircuitState.OPEN.value:
                # Continuing failures while OPEN — extend/refresh open_until cooldown
                state_data["open_until"] = (now + timedelta(seconds=open_duration)).isoformat()
                state_data["last_state_change_at"] = now.isoformat()
            elif state == CircuitState.CLOSED.value:
                failures = state_data.get("failure_count", 0) + 1
                state_data["failure_count"] = failures
                threshold = state_data.get("failure_threshold", settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD)

                if failures >= threshold:
                    logger.warning(
                        "Circuit breaker for '%s' reached failure threshold (%d/%d). Transitioning to OPEN for %ds.",
                        dependency_key,
                        failures,
                        threshold,
                        open_duration,
                    )
                    state_data["state"] = CircuitState.OPEN.value
                    state_data["open_until"] = (now + timedelta(seconds=open_duration)).isoformat()
                    state_data["last_state_change_at"] = now.isoformat()

            await self._save_state(dependency_key, state_data)

    async def get_circuit_state(self, dependency_key: str) -> dict[str, Any]:
        """Returns the current structured circuit state for a single dependency."""
        async with self._lock:
            return await self._load_state(dependency_key)

    async def list_all_circuits(self) -> list[dict[str, Any]]:
        """Returns all circuit breakers tracked across database and memory."""
        circuits: dict[str, dict[str, Any]] = dict(self._local_cache)

        # Merge with database records
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(CircuitBreakerRecord)
                res = await session.execute(stmt)
                records = res.scalars().all()
                for rec in records:
                    circuits[rec.dependency_key] = {
                        "dependency_key": rec.dependency_key,
                        "dependency_type": rec.dependency_type,
                        "state": rec.state,
                        "failure_count": rec.failure_count,
                        "success_count": rec.success_count,
                        "consecutive_successes": rec.consecutive_successes,
                        "last_failure_at": rec.last_failure_at.isoformat() if rec.last_failure_at else None,
                        "last_success_at": rec.last_success_at.isoformat() if rec.last_success_at else None,
                        "last_state_change_at": rec.last_state_change_at.isoformat() if rec.last_state_change_at else None,
                        "open_until": rec.open_until.isoformat() if rec.open_until else None,
                        "failure_threshold": rec.failure_threshold,
                        "open_duration_seconds": rec.open_duration_seconds,
                        "half_open_max_probes": rec.half_open_max_probes,
                        "last_failure_reason": rec.last_failure_reason,
                    }
        except Exception as exc:
            logger.debug("Database circuit listing failed: %s", exc)

        return list(circuits.values())

    async def manual_reset(self, dependency_key: str) -> dict[str, Any]:
        """Manually forces a circuit breaker back to CLOSED status."""
        async with self._lock:
            state_data = await self._load_state(dependency_key)
            now = datetime.now(timezone.utc)
            state_data["state"] = CircuitState.CLOSED.value
            state_data["failure_count"] = 0
            state_data["consecutive_successes"] = 0
            state_data["half_open_active_probes"] = 0
            state_data["open_until"] = None
            state_data["last_state_change_at"] = now.isoformat()
            await self._save_state(dependency_key, state_data)
            logger.info("Manually reset circuit breaker for '%s' to CLOSED.", dependency_key)
            return state_data

    # -----------------------------------------------------------------------
    # Internal Storage (Redis -> In-Memory Cache -> PostgreSQL Persistence)
    # -----------------------------------------------------------------------

    async def _load_state(self, dependency_key: str, dependency_type: str = "general") -> dict[str, Any]:
        # 1. Check Redis if available
        redis_client = await self._get_redis_client()
        if redis_client:
            try:
                raw = await redis_client.get(f"circuit:{dependency_key}")
                if raw:
                    data = json.loads(raw)
                    self._local_cache[dependency_key] = data
                    return data
            except Exception as exc:
                logger.debug("Redis circuit read error: %s", exc)
                self._redis_client = None
                self._redis_disabled = True

        # 2. Check local memory
        if dependency_key in self._local_cache:
            return self._local_cache[dependency_key]

        # 3. Check DB
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(CircuitBreakerRecord).where(CircuitBreakerRecord.dependency_key == dependency_key)
                res = await session.execute(stmt)
                rec = res.scalar_one_or_none()
                if rec:
                    data = {
                        "dependency_key": rec.dependency_key,
                        "dependency_type": rec.dependency_type,
                        "state": rec.state,
                        "failure_count": rec.failure_count,
                        "success_count": rec.success_count,
                        "consecutive_successes": rec.consecutive_successes,
                        "last_failure_at": rec.last_failure_at.isoformat() if rec.last_failure_at else None,
                        "last_success_at": rec.last_success_at.isoformat() if rec.last_success_at else None,
                        "last_state_change_at": rec.last_state_change_at.isoformat() if rec.last_state_change_at else None,
                        "open_until": rec.open_until.isoformat() if rec.open_until else None,
                        "failure_threshold": rec.failure_threshold,
                        "open_duration_seconds": rec.open_duration_seconds,
                        "half_open_max_probes": rec.half_open_max_probes,
                        "success_threshold": settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
                        "last_failure_reason": rec.last_failure_reason,
                    }
                    self._local_cache[dependency_key] = data
                    return data
        except Exception:
            pass

        # 4. Initialize fresh CLOSED state
        now = datetime.now(timezone.utc)
        defaults = self._get_defaults()
        init_data = {
            "dependency_key": dependency_key,
            "dependency_type": dependency_type,
            "state": CircuitState.CLOSED.value,
            "failure_count": 0,
            "success_count": 0,
            "consecutive_successes": 0,
            "last_failure_at": None,
            "last_success_at": None,
            "last_state_change_at": now.isoformat(),
            "open_until": None,
            "failure_threshold": defaults["failure_threshold"],
            "open_duration_seconds": defaults["open_duration_seconds"],
            "half_open_max_probes": defaults["half_open_max_probes"],
            "success_threshold": defaults["success_threshold"],
            "last_failure_reason": None,
        }
        self._local_cache[dependency_key] = init_data
        return init_data

    async def _save_state(self, dependency_key: str, data: dict[str, Any]) -> None:
        self._local_cache[dependency_key] = data

        # Write to Redis
        redis_client = await self._get_redis_client()
        if redis_client:
            try:
                ttl = data.get("open_duration_seconds", settings.CIRCUIT_BREAKER_OPEN_SECONDS) * 2
                await redis_client.set(f"circuit:{dependency_key}", json.dumps(data), ex=max(300, ttl))
            except Exception as exc:
                logger.debug("Redis circuit write error: %s", exc)
                self._redis_client = None
                self._redis_disabled = True

        # Persist asynchronously to DB
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(CircuitBreakerRecord).where(CircuitBreakerRecord.dependency_key == dependency_key)
                res = await session.execute(stmt)
                rec = res.scalar_one_or_none()
                now = datetime.now(timezone.utc)

                def _parse_dt(val: Any) -> datetime | None:
                    if not val:
                        return None
                    if isinstance(val, str):
                        dt = datetime.fromisoformat(val)
                        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                    return val

                if not rec:
                    rec = CircuitBreakerRecord(
                        dependency_key=dependency_key,
                        dependency_type=data.get("dependency_type", "general"),
                        state=data["state"],
                        failure_count=data.get("failure_count", 0),
                        success_count=data.get("success_count", 0),
                        consecutive_successes=data.get("consecutive_successes", 0),
                        last_failure_at=_parse_dt(data.get("last_failure_at")),
                        last_success_at=_parse_dt(data.get("last_success_at")),
                        last_state_change_at=_parse_dt(data.get("last_state_change_at")) or now,
                        open_until=_parse_dt(data.get("open_until")),
                        failure_threshold=data.get("failure_threshold", settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD),
                        open_duration_seconds=data.get("open_duration_seconds", settings.CIRCUIT_BREAKER_OPEN_SECONDS),
                        half_open_max_probes=data.get("half_open_max_probes", settings.CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS),
                        last_failure_reason=data.get("last_failure_reason"),
                        circuit_metadata={},
                    )
                    session.add(rec)
                else:
                    rec.state = data["state"]
                    rec.failure_count = data.get("failure_count", 0)
                    rec.success_count = data.get("success_count", 0)
                    rec.consecutive_successes = data.get("consecutive_successes", 0)
                    rec.last_failure_at = _parse_dt(data.get("last_failure_at"))
                    rec.last_success_at = _parse_dt(data.get("last_success_at"))
                    rec.last_state_change_at = _parse_dt(data.get("last_state_change_at")) or now
                    rec.open_until = _parse_dt(data.get("open_until"))
                    rec.last_failure_reason = data.get("last_failure_reason")

                await session.commit()
        except Exception as exc:
            logger.debug("Database circuit record write error: %s", exc)


circuit_breaker = CircuitBreaker()
