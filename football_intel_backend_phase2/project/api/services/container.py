"""Composition of services (built once at startup by the application factory)."""
from __future__ import annotations

from dataclasses import dataclass

from api.repositories.database import Database
from api.services.freshness_service import FreshnessService
from api.services.health_service import HealthService
from api.services.integrity_service import build_runtime
from api.services.player_service import PlayerService
from api.services.search_service import SearchService
from api.services.runtime import RuntimeHolder


@dataclass
class Services:
    db: Database
    runtime: RuntimeHolder
    health: HealthService
    freshness: FreshnessService
    search: SearchService
    player: PlayerService


def start_services(db_path) -> Services:
    db = Database(db_path)
    holder = RuntimeHolder(build_runtime(db))
    return Services(db, holder, HealthService(holder, db), FreshnessService(holder, db), SearchService(holder, db), PlayerService(holder, db))
