"""
SQS message DTOs for diagrams-v2.
Message format matches v1 (diagrams/app/models/sqs_models.py) exactly.
"""

from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel


class AIOperationType(str, Enum):
    """V1-compatible operation type names."""
    UPLOAD_REPO = "upload_repo"
    GENERATE_IR = "generate_ir"
    GENERATE_DIAGRAM = "generate_diagram"
    GENERATE_DESCRIPTION = "generate_description"
    REGENERATE_DIAGRAM = "regenerate_diagram"
    INGEST_DEPENDENCY = "ingest_dependency"
    DELETE_DEPENDENCY = "delete_dependency"
    UPLOAD_FILE = "upload_file"          # v1 name for document ingestion
    DELETE_FILE = "delete_file"
    # generate_diagram_batch is NOT a v1 operation.
    # When multiple types are selected, the API dispatches N individual generate_diagram messages.


# Backward-compatible alias
DiagramOperationType = AIOperationType


class AIResponseStatus(str, Enum):
    """V1-compatible response status values."""
    OK = "OK"
    ERROR = "ERROR"


class InferenceServerType(str, Enum):
    ON_PREM = "ON_PREM"
    BEDROCK = "BEDROCK"
    ANTHROPIC = "ANTHROPIC"


class AIRequestMessageDto(BaseModel):
    """Request message — matches v1 AIRequestMessageDto exactly (camelCase fields)."""
    projectId: Optional[str] = None
    data: Optional[Any] = None
    operation: Optional[AIOperationType | str] = None
    timestamp: Optional[int] = 0
    inferenceServer: Optional[InferenceServerType] = InferenceServerType.ON_PREM
    userId: Optional[str] = None
    organizationId: Optional[str] = None
    # Internal tracking (not in v1 but harmless extras)
    job_id: Optional[str] = None
    request_id: Optional[str] = None


class AIResponseMessageDto(BaseModel):
    """Response message — matches v1 AIResponseMessageDto exactly (camelCase fields)."""
    status: AIResponseStatus | str
    error: Optional[str] = None
    processingTime: Optional[int] = None   # milliseconds (int), matches v1
    timestamp: int = 0
    projectId: Optional[str] = None
    operation: Optional[AIOperationType | str] = None
    data: Optional[Any] = None
    userId: Optional[str] = None
    organizationId: Optional[str] = None
    # Internal tracking (not in v1 but harmless extras)
    job_id: Optional[str] = None
    request_id: Optional[str] = None


# Legacy aliases for code that still uses old names
DiagramRequestMessageDto = AIRequestMessageDto
DiagramResponseMessageDto = AIResponseMessageDto


class DiagramJobStatus(str, Enum):
    """Job status for the disk-persisted job file (not SQS wire format)."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"
