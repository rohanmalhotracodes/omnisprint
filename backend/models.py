from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class Subtask(BaseModel):
    subtask: Optional[str] = None
    status: Optional[str] = None
    assignee: Optional[str] = None
    estimated_completion_date: Optional[str] = None
    notes: Optional[str] = None
    github_issue_numbers: List[int] = Field(default_factory=list)
    github_pr_numbers: List[int] = Field(default_factory=list)
    raw_row: Dict[str, Any] = Field(default_factory=dict)


class Project(BaseModel):
    project_id: str
    project_name: str
    project_description: Optional[str] = None
    project_owner_lead: Optional[str] = None
    project_owner_contributor: Optional[str] = None
    planned_completion_date: Optional[str] = None
    subtasks: List[Subtask] = Field(default_factory=list)
    all_github_issue_numbers: List[int] = Field(default_factory=list)
    all_github_pr_numbers: List[int] = Field(default_factory=list)
    raw_project_rows: List[Dict[str, Any]] = Field(default_factory=list)
    debugging_doc_link: Optional[str] = None
    source_mode: Optional[str] = "LIVE"


class RiskReport(BaseModel):
    project_id: str
    project_name: str
    risk_score: int
    risk_level: str
    risk_drivers: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    high_risk_subtasks: List[Dict[str, Any]] = Field(default_factory=list)
    github_issue_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    github_pr_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_by_source: Dict[str, Any] = Field(default_factory=dict)
    open_linked_issue_count: int = 0
    open_linked_pr_count: int = 0
    stale_open_issue_count: int = 0
    stale_open_pr_count: int = 0
    failing_ci_pr_count: int = 0
    flaky_ci_pr_count: int = 0
    failed_tests_total: int = 0
    flaky_tests_total: int = 0
    stale_ci_signal_count: int = 0
    ci_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    issue_pr_links: List[Dict[str, Any]] = Field(default_factory=list)
