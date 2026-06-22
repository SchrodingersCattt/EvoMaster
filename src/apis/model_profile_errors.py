from src.services.llm_profile_validation import InvalidModelProfileError
from src.utils.exceptions import BaseErrorResponse


def invalid_model_profile_error(exc: InvalidModelProfileError) -> BaseErrorResponse:
    return BaseErrorResponse(
        http_status=422,
        code=422,
        msg=f"模型 {exc.profile_key} 不可用，请重新选择模型。",
        data={
            "error_code": "INVALID_MODEL_PROFILE",
            "profile": exc.profile_key,
            "available_profiles": list(exc.available_profiles),
        },
    )
