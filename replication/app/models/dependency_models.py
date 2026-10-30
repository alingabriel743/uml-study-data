"""
Dependency ingestion models — ported from v1 with v2 conventions.
Weaviate references removed; agent-based analysis replaces vectorization.
"""

from pydantic import BaseModel, Field
from typing import Optional, List

# Valid values (from webdev team spec)
UPLOAD_METHODS = ["link", "file", "github", "gitlab", "bitbucket"]
DEPENDENCY_TYPES = ["internal", "external"]
USAGE_TYPES = [
    "backendFramework",
    "frontendFramework",
    "databaseConnector",
    "authenticationModule",
    "buildTool",
    "testingFramework",
    "devopsCiTool",
    "legacyUiToolkit",
    "loggingMonitoring",
    "utilityHelper",
    "customDependency",
]


class DependencyDataDto(BaseModel):
    """Dependency source data — exact from webdev team spec."""

    # For link & git-provider uploads
    url: Optional[str] = Field(
        None, description="URL for link, github, gitlab, bitbucket uploads"
    )
    # For file uploads
    fileName: Optional[str] = Field(None, description="Original filename")
    fileLocation: Optional[str] = Field(None, description="Location in S3")


class DependencyDto(BaseModel):
    """Main dependency DTO — exact from webdev team spec."""

    id: Optional[str] = Field(None, description="MongoDB ObjectId as string")
    type: str = Field(..., description="internal | external")
    name: str = Field(..., description="Dependency name")
    usageType: str = Field(..., description="One of USAGE_TYPES")
    uploadMethod: str = Field(..., description="One of UPLOAD_METHODS")
    data: DependencyDataDto = Field(..., description="Source location data")
    token: Optional[str] = Field(
        None,
        description="Access token for private repositories (encrypted or plain text)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "507f1f77bcf86cd799439011",
                    "type": "external",
                    "name": "Spring Framework",
                    "usageType": "backendFramework",
                    "uploadMethod": "github",
                    "data": {
                        "url": "https://github.com/spring-projects/spring-framework.git"
                    },
                },
                {
                    "id": "507f1f77bcf86cd799439012",
                    "type": "internal",
                    "name": "Private Auth Module",
                    "usageType": "authenticationModule",
                    "uploadMethod": "github",
                    "data": {
                        "url": "https://github.com/company/private-auth-module.git"
                    },
                    "token": "iv:auth_tag:encrypted_token_data",
                },
            ]
        }
    }


# --- API request / response models ---


class DependencyIngestionRequest(BaseModel):
    """Request body for POST /ai/ingest_dependencies/{project_id}."""

    dependencies: List[DependencyDto] = Field(
        ..., description="List of dependencies to ingest"
    )


class DependencyIngestionResponse(BaseModel):
    """Response from POST /ai/ingest_dependencies/{project_id}."""

    status: str = Field(..., description="ok | error")
    project_id: str
    processed_dependencies: int = Field(
        ..., description="Number of successfully processed dependencies"
    )
    failed_dependencies: Optional[List[str]] = Field(
        None, description="Names of dependencies that failed"
    )
    message: str
