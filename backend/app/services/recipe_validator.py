"""Validation and compatibility rules for UI templates and recipe definitions."""

from typing import Any

from app.core.exceptions import ValidationError
from app.models.county_source import SourceConfiguration, UITemplate

ALLOWED_LOCATOR_STRATEGIES = {
    "id",
    "css",
    "xpath",
    "name",
    "label",
    "role",
    "text",
    "placeholder",
    "test_id",
}

ALLOWED_SEARCH_METHODS = {
    "FORM_SUBMIT",
    "SPA_INTERACTION",
    "GET",
    "POST",
}

ALLOWED_RESULT_TYPES = {
    "table",
    "cards",
    "detail_page",
    "json_api",
}

DANGEROUS_PATTERNS = ["<script", "javascript:", "eval(", "exec(", "__import__", "subprocess"]


def validate_recipe_definition(definition: dict[str, Any]) -> None:
    """Validate declarative recipe JSON structure against allowed strategies and safety rules.

    Raises:
        ValidationError: If schema is invalid, uses unsupported locators, or contains unsafe tokens.
    """
    if not isinstance(definition, dict):
        raise ValidationError("Recipe definition must be a JSON object", code="INVALID_RECIPE_SCHEMA")

    # Safety check: No executable code or script injection
    raw_str = str(definition).lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in raw_str:
            raise ValidationError(
                f"Recipe definition contains forbidden pattern: {pattern}",
                code="UNSAFE_RECIPE_DEFINITION",
            )

    # 1. Search block validation (optional in draft, required if present)
    if "search" in definition:
        search = definition["search"]
        if not isinstance(search, dict):
            raise ValidationError("'search' must be an object", code="INVALID_RECIPE_SEARCH")

        method = search.get("method")
        if method and method not in ALLOWED_SEARCH_METHODS:
            raise ValidationError(
                f"Unsupported search method '{method}'. Allowed: {sorted(ALLOWED_SEARCH_METHODS)}",
                code="UNSUPPORTED_SEARCH_METHOD",
            )

        fields = search.get("fields")
        if fields is not None:
            if not isinstance(fields, list):
                raise ValidationError("'search.fields' must be a list", code="INVALID_SEARCH_FIELDS")

            for idx, field in enumerate(fields):
                if not isinstance(field, dict):
                    raise ValidationError(
                        f"Field at index {idx} must be an object",
                        code="INVALID_FIELD_DEFINITION",
                    )
                if "name" not in field:
                    raise ValidationError(
                        f"Field at index {idx} is missing 'name'",
                        code="MISSING_FIELD_NAME",
                    )
                locator = field.get("locator")
                if locator is not None:
                    if not isinstance(locator, dict):
                        raise ValidationError(
                            f"Field '{field['name']}' locator must be an object",
                            code="INVALID_LOCATOR",
                        )
                    strategy = locator.get("strategy")
                    if strategy not in ALLOWED_LOCATOR_STRATEGIES:
                        raise ValidationError(
                            f"Unsupported locator strategy '{strategy}' in field '{field['name']}'. Allowed: {sorted(ALLOWED_LOCATOR_STRATEGIES)}",
                            code="UNSUPPORTED_LOCATOR_STRATEGY",
                        )
                    if not locator.get("value"):
                        raise ValidationError(
                            f"Locator for field '{field['name']}' is missing 'value'",
                            code="MISSING_LOCATOR_VALUE",
                        )

        # Submit button validation
        submit = search.get("submit")
        if submit is not None:
            if not isinstance(submit, dict):
                raise ValidationError("'search.submit' must be an object", code="INVALID_SUBMIT_BUTTON")
            submit_strategy = submit.get("strategy")
            if submit_strategy and submit_strategy not in ALLOWED_LOCATOR_STRATEGIES:
                raise ValidationError(
                    f"Unsupported submit locator strategy '{submit_strategy}'. Allowed: {sorted(ALLOWED_LOCATOR_STRATEGIES)}",
                    code="UNSUPPORTED_LOCATOR_STRATEGY",
                )

    # 2. Results block validation
    if "results" in definition:
        results = definition["results"]
        if not isinstance(results, dict):
            raise ValidationError("'results' must be an object", code="INVALID_RESULTS_DEFINITION")
        res_type = results.get("type")
        if res_type and res_type not in ALLOWED_RESULT_TYPES:
            raise ValidationError(
                f"Unsupported results type '{res_type}'. Allowed: {sorted(ALLOWED_RESULT_TYPES)}",
                code="UNSUPPORTED_RESULT_TYPE",
            )

    # 3. Field mappings validation
    if "field_mappings" in definition:
        mappings = definition["field_mappings"]
        if not isinstance(mappings, dict):
            raise ValidationError("'field_mappings' must be an object", code="INVALID_FIELD_MAPPINGS")


def check_template_compatibility(
    template: UITemplate, config: SourceConfiguration
) -> tuple[bool, list[str]]:
    """Verify that a SourceConfiguration is functionally compatible with a UITemplate.

    Returns:
        (is_compatible: bool, errors: list[str])
    """
    errors: list[str] = []

    # 1. Source type compatibility
    supported_types = template.supported_source_types or ["assessor", "tax"]
    if config.source_type not in supported_types:
        errors.append(
            f"Configuration source_type '{config.source_type}' is not supported by template '{template.name}' (supported: {supported_types})"
        )

    # 2. Search method compatibility
    supported_methods = template.supported_search_methods or list(ALLOWED_SEARCH_METHODS)
    if config.search_method and config.search_method not in supported_methods:
        errors.append(
            f"Search method '{config.search_method}' is not supported by template '{template.name}' (supported: {supported_methods})"
        )

    # 3. Required configuration fields
    required_fields = template.required_configuration_fields or []
    config_fields = config.search_fields or {}
    for req in required_fields:
        if req not in config_fields:
            errors.append(f"Template requires field '{req}' but it is missing in the source configuration")

    # 4. Locator strategy validation on config fields
    for field_name, field_def in config_fields.items():
        if isinstance(field_def, dict) and "strategy" in field_def:
            strategy = field_def["strategy"]
            if strategy not in ALLOWED_LOCATOR_STRATEGIES:
                errors.append(
                    f"Configuration field '{field_name}' uses unsupported locator strategy '{strategy}'"
                )

    return (len(errors) == 0, errors)

