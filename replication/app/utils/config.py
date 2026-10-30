"""
Configuration for diagrams-v2.
Uses pydantic-settings for environment variable loading with APP_ prefix.
Same pattern as atonis-req-copilot.
"""

import os
from pydantic_settings import BaseSettings

from app.utils.logger import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables with APP_ prefix."""

    # FastAPI
    fastapi_host: str = "127.0.0.1"
    fastapi_port: int = 8000

    # Environment
    local_environment: bool = True

    # AWS Configuration (credentials loaded from ~/.aws/credentials)
    aws_region: str = "us-east-1"

    # AWS Bedrock Configuration
    use_bedrock: bool = True
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6"

    # Anthropic Direct API (alternative to Bedrock)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Diagram Configuration
    diagram_max_elements: int = 50
    max_diagrams_per_type: int = 12
    max_diagram_concurrency: int = 10  # Max parallel DiagramAgents in Phase 3 (0 = unlimited)

    # PlantUML compiler used as the independent syntax validator (CLI wrapper or `java -jar plantuml.jar`)
    plantuml_command: str = "plantuml"
    plantuml_timeout_seconds: float = 60.0

    # Usage Reporting (token consumption tracking — matches v1)
    usage_record_url: str = "https://codeiq.ness-ie-practice.com/api/usage/record"
    jwt_token_url: str = ""
    jwt_client_id: str = ""
    jwt_client_secret: str = ""

    # SQS Configuration
    sqs_enabled: bool = False  # Set to True to use SQS async mode
    sqs_request_queue_name: str = "diagrams-requests.fifo"
    sqs_response_queue_name: str = "diagrams-responses.fifo"
    localstack_url: str = "http://localhost:4566"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Job results are stored in data/{project_id}/jobs/{job_id}.json

    # Weaviate (local Docker — used when APP_LOCAL_ENVIRONMENT=true)
    weaviate_enabled: bool = False
    weaviate_http_host: str = "localhost"
    weaviate_http_port: int = 8081
    weaviate_http_secure: bool = False
    weaviate_grpc_host: str = "localhost"
    weaviate_grpc_port: int = 50051
    weaviate_grpc_secure: bool = False

    # KB Service (deployed environment — takes precedence over Weaviate when enabled)
    kb_service_enabled: bool = False
    kb_request_queue_name: str = "codecatalyst-dev-knowledge-base-request-queue"
    kb_response_queue_name: str = "codecatalyst-dev-knowledge-base-response-queue"
    kb_response_timeout: float = 300.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "APP_"
        case_sensitive = False
        extra = "ignore"  # .env also holds non-APP_ secrets (e.g. AWS_BEARER_TOKEN_BEDROCK)


settings = Settings()


def load_config() -> dict:
    """
    Load configuration as a dict (backward compatible with existing code).
    """
    config = {
        "fastapi": {
            "host": settings.fastapi_host,
            "port": settings.fastapi_port,
        },
        "anthropic": {
            "api_key": settings.anthropic_api_key,
            "model": settings.anthropic_model,
        },
        "local": {
            "environment": settings.local_environment,
        },
        "aws": {
            "region": settings.aws_region,
        },
        "bedrock": {
            "use_bedrock": settings.use_bedrock,
            "model_id": settings.bedrock_model_id,
        },
        "files": {
            "code_extensions": [
                ".sql", ".plsql", ".py", ".java", ".js", ".ts",
                ".cs", ".cpp", ".cc", ".cxx", ".hpp", ".h",
                ".kt", ".cob", ".cbl", ".php",
            ],
        },
        "supported": {
            "languages": {
                "available": [
                    "sql", "java", "python", "javascript", "csharp",
                    "cpp", "kotlin", "cobol", "php", "perl",
                ],
                "default": "sql",
            },
        },
        "diagram": {
            "supported_types": [
                "class", "sequence", "use_case",
                "activity", "component", "deployment",
                "system_context",
            ],
            "max_elements_per_diagram": settings.diagram_max_elements,
        },
    }

    logger.info("Configuration loaded successfully")
    return config


def get_anthropic_config() -> dict:
    """Get Anthropic-specific configuration."""
    return {
        "api_key": settings.anthropic_api_key,
        "model": settings.anthropic_model,
    }


def get_supported_languages() -> list:
    """Get list of supported programming languages."""
    return [
        "sql", "java", "python", "javascript", "csharp",
        "cpp", "kotlin", "cobol", "php", "perl",
    ]


def get_supported_diagram_types() -> list:
    """Get list of supported diagram types."""
    return [
        "class", "sequence", "use_case",
        "activity", "component", "deployment",
        "system_context",
    ]
