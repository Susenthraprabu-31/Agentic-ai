"""Browser automation package using Playwright."""

from app.browser.browser_manager import BrowserManager, get_browser_context, get_browser_manager
from app.browser.evidence_collector import EvidenceCollector
from app.browser.navigation import navigate_portal_step_by_step
from app.browser.pagination_handler import PaginationHandler, PaginationStats
from app.browser.recipe_loader import RecipeLoader, RecipeLoadException, ResolvedRecipe
from app.browser.result_extractor import ResultExtractor
from app.browser.security_detector import SecurityDetectionResult, SecurityDetector
from app.browser.step_runner import (
    PlaywrightStepRunner,
    SecurityChallengeException,
    StepExecutionException,
)
from app.browser.url_validator import SSRFProtectionException, URLValidator

__all__ = [
    "BrowserManager",
    "EvidenceCollector",
    "PaginationHandler",
    "PaginationStats",
    "PlaywrightStepRunner",
    "RecipeLoadException",
    "RecipeLoader",
    "ResolvedRecipe",
    "ResultExtractor",
    "SSRFProtectionException",
    "SecurityChallengeException",
    "SecurityDetectionResult",
    "SecurityDetector",
    "StepExecutionException",
    "URLValidator",
    "get_browser_context",
    "get_browser_manager",
    "navigate_portal_step_by_step",
]
