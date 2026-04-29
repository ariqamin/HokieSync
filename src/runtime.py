from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings, load_settings
from src.db import Database
from src.providers.composite_provider import CompositeProvider
from src.providers.grade_provider import GradeProvider
from src.providers.mock_data import MockDataProvider
from src.providers.rmp_provider import RMPProvider
from src.providers.vt_catalog import VTCatalogProvider
from src.services.dars_service import DARSService
from src.services.free_time_service import FreeTimeService
from src.services.preference_service import PreferenceService
from src.services.privacy_service import PrivacyService
from src.services.recommendation_service import RecommendationService
from src.services.schedule_service import ScheduleService
from src.services.watch_service import WatchService


@dataclass(slots=True)
class BotRuntime:
    settings: Settings
    db: Database
    mock_provider: MockDataProvider
    catalog_provider: VTCatalogProvider | None
    rmp_provider: RMPProvider | None
    grade_provider: GradeProvider | None
    provider: CompositeProvider
    privacy_service: PrivacyService
    schedule_service: ScheduleService
    free_time_service: FreeTimeService
    watch_service: WatchService
    recommendation_service: RecommendationService
    preference_service: PreferenceService
    dars_service: DARSService


def create_runtime() -> BotRuntime:
    settings = load_settings()
    db = Database(settings.database_path)
    mock_provider = MockDataProvider(settings.mock_catalog_path)

    catalog_provider = _create_catalog_provider(settings)
    rmp_provider = _create_rmp_provider(settings, db)
    grade_provider = _create_grade_provider(settings, db)
    provider = CompositeProvider(
        catalog_provider=catalog_provider,
        rmp_provider=rmp_provider,
        grade_provider=grade_provider,
        mock_provider=mock_provider,
    )

    return BotRuntime(
        settings=settings,
        db=db,
        mock_provider=mock_provider,
        catalog_provider=catalog_provider,
        rmp_provider=rmp_provider,
        grade_provider=grade_provider,
        provider=provider,
        privacy_service=PrivacyService(db),
        schedule_service=ScheduleService(db),
        free_time_service=FreeTimeService(db),
        watch_service=WatchService(db, provider),
        recommendation_service=RecommendationService(db, provider),
        preference_service=PreferenceService(),
        dars_service=DARSService(),
    )


def _create_catalog_provider(settings: Settings) -> VTCatalogProvider | None:
    if settings.catalog_provider not in {"auto", "vt", "pyvt"}:
        return None

    vt_provider = VTCatalogProvider(settings.vt_preferred_term, settings.vt_term_year)
    if settings.catalog_provider in {"vt", "pyvt"} or vt_provider.available:
        return vt_provider
    return None


def _create_rmp_provider(settings: Settings, db: Database) -> RMPProvider | None:
    if settings.rmp_provider == "none":
        return None
    return RMPProvider(
        db=db,
        graphql_url=settings.rmp_graphql_url,
        auth_token=settings.rmp_auth_token,
        school_name=settings.rmp_school_name,
        school_id=settings.rmp_school_id,
    )


def _create_grade_provider(settings: Settings, db: Database) -> GradeProvider | None:
    if settings.grades_provider == "none":
        return None
    return GradeProvider(
        db=db,
        csv_path=settings.grades_csv_path,
        json_path=settings.grades_json_path,
        request_url=settings.grades_request_url,
        headers=settings.grades_headers,
        cookies=settings.grades_cookies,
    )
