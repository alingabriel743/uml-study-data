"""
Pydantic models for diagrams-v2 API requests and responses.
Simplified from v1 — no SQS models, no Weaviate, no inference server types.
"""

from pydantic import BaseModel, model_validator, Field
from typing import Dict, Optional, Any, List


# --- Repository Upload ---

class RepoInput(BaseModel):
    """Input for repository upload endpoint."""
    url: Optional[str] = None  # GitHub URL
    location: Optional[str] = None  # Local path or S3 ZIP location
    language: str = "sql"
    token: Optional[str] = None  # GitHub token for private repos

    @model_validator(mode="after")
    def validate_source(self):
        if not self.url and not self.location:
            raise ValueError("Either 'url' or 'location' must be provided")
        return self


class LanguageCodeMetrics(BaseModel):
    language: str
    files: int
    lines: int
    percentage: float


class CodeMetrics(BaseModel):
    total_lines: int
    total_files: int
    total_code_files: int
    language_breakdown: List[LanguageCodeMetrics]
    language_count: int


class LanguageDistribution(BaseModel):
    language: str
    file_count: int
    percentage: float


class RepoResponse(BaseModel):
    """Response from repository upload endpoint."""
    status: str
    project_id: str
    language: str
    detected_language: Optional[str] = None
    mapped_language: Optional[str] = None
    main_language: Optional[str] = None
    language_distribution: Optional[List[LanguageDistribution]] = []
    total_files: Optional[int] = None
    detection_confidence: Optional[str] = None
    source_type: Optional[str] = None  # "github", "zip", or "local"
    code_metrics: Optional[CodeMetrics] = None
    total_lines_of_code: Optional[int] = None
    lines_of_code_by_language: Optional[List[LanguageCodeMetrics]] = None


# --- IR Generation ---

class IRGenerationRequest(BaseModel):
    """Request for IR generation endpoint."""
    language: str = "sql"
    options: Optional[Dict[str, Any]] = None


class IRGenerationResponse(BaseModel):
    """Response from IR generation endpoint."""
    status: str
    project_id: str
    ir_file: str
    stats: Dict[str, int]


# --- Diagram Generation (NEW) ---

class DiagramGenerationRequest(BaseModel):
    """Request for diagram generation via Claude agent."""
    diagram_type: str = Field(
        default="class",
        description="Type of UML diagram to generate",
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional options for diagram generation",
    )


class DiagramOutput(BaseModel):
    """A single generated diagram."""
    diagram_type: str
    plantuml_code: str
    element_count: int = 0
    filename: Optional[str] = None


class DiagramGenerationResponse(BaseModel):
    """Response from diagram generation endpoint."""
    status: str
    project_id: str
    diagram_type: str
    diagrams: List[DiagramOutput] = []
    agent_messages: Optional[List[str]] = None
    error: Optional[str] = None


# --- Batch Diagram Generation ---

class BatchDiagramRequest(BaseModel):
    """Request to generate multiple diagram types in parallel."""
    diagram_types: List[str] = Field(
        description="List of diagram types to generate (e.g., ['class', 'sequence', 'component'])",
    )
    options: Optional[Dict[str, Any]] = None


class BatchDiagramTypeResult(BaseModel):
    """Result for a single diagram type within a batch."""
    diagram_type: str
    status: str
    diagrams: List[DiagramOutput] = []
    error: Optional[str] = None
    duration_seconds: float = 0


class BatchDiagramResponse(BaseModel):
    """Response from batch diagram generation."""
    status: str
    project_id: str
    results: List[BatchDiagramTypeResult] = []
    total_diagrams: int = 0
    total_duration_seconds: float = 0
