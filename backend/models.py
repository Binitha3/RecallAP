"""Typed API and domain models for RecallAP."""

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


def mask_account(value: str) -> str:
    """Mask an account value while retaining only its last four characters."""
    trimmed = value.strip()
    if not trimmed or trimmed.startswith("XXXX"):
        return trimmed
    digits = "".join(character for character in trimmed if character.isalnum())
    return f"XXXX{digits[-4:]}" if digits else ""


class APIModel(BaseModel):
    """Base model configured for the public RecallAP JSON contract."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class InvoiceLine(APIModel):
    item: str
    qty: float
    unit_price: float


class Invoice(APIModel):
    id: str
    vendor_id: str
    po_number: str = ""
    invoice_number: str
    date: str
    currency: str
    gstin: str = ""
    gst_rate: float = 0
    bank_account: str = ""
    lines: list[InvoiceLine] = Field(default_factory=list)
    total: float
    seq: int

    @field_validator("bank_account")
    @classmethod
    def mask_bank_account(cls, value: str) -> str:
        return mask_account(value)


class PurchaseOrder(APIModel):
    po_number: str
    vendor_id: str
    total: float
    currency: str
    lines: list[InvoiceLine] = Field(default_factory=list)


class GoodsReceiptLine(APIModel):
    item: str
    qty: float


class GoodsReceiptNote(APIModel):
    po_number: str
    received: list[GoodsReceiptLine] = Field(default_factory=list)


class Vendor(APIModel):
    id: str
    name: str
    gstin: str = ""
    bank_account: str = ""
    payment_terms: str = ""
    category: str = ""
    currency: str = "INR"

    @field_validator("bank_account")
    @classmethod
    def mask_bank_account(cls, value: str) -> str:
        return mask_account(value)


class ExceptionSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InvoiceException(APIModel):
    type: str
    detail: str
    severity: ExceptionSeverity
    variance_pct: float | None = None


class InvoiceSummary(APIModel):
    id: str
    vendor_name: str
    amount: float
    currency: str
    status: str
    exception_types: list[str] = Field(default_factory=list)


class InvoiceDetailResponse(APIModel):
    invoice: Invoice
    po: PurchaseOrder | None
    grn: GoodsReceiptNote | None
    vendor: Vendor
    exceptions: list[InvoiceException] = Field(default_factory=list)


class HumanResolutionRequest(APIModel):
    action: str
    note: str = ""
    approver: str
    reusable_precedent: bool = True

    @field_validator("action", "approver")
    @classmethod
    def required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned


class HumanResolutionResponse(APIModel):
    case_id: str
    status: str
    retained: bool


class DemoResetRequest(APIModel):
    clear_memory: bool = False


class DemoResetResponse(APIModel):
    ok: bool


class MemorySearchResult(APIModel):
    case_id: str | None = None
    type: str
    text: str


class VendorBriefingResponse(APIModel):
    vendor_id: str
    briefing_text: str


class MetricPoint(APIModel):
    n: int
    rolling_rate: float
    auto_correct: bool
    human_touch: bool


class MetricSummary(APIModel):
    final_rate: float = 0
    touchless_correct: int = 0
    unsafe: int = 0
    human_touches: int = 0
    invoices: int = 0
    processed: int = 0
    touchless: int = 0
    human_touch: int = 0
    auto_correct: float = 0


class MetricsResponse(APIModel):
    points: list[MetricPoint] = Field(default_factory=list)
    summary: MetricSummary = Field(default_factory=MetricSummary)


class DecisionAction(StrEnum):
    AUTO_APPROVE = "AUTO_APPROVE"
    AUTO_APPROVE_WITH_NOTE = "AUTO_APPROVE_WITH_NOTE"
    REQUEST_VENDOR_INFO = "REQUEST_VENDOR_INFO"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class ProposedDecision(APIModel):
    action: DecisionAction
    confidence: float = Field(ge=0, le=1)
    rationale: str
    cited_case_ids: list[str] = Field(default_factory=list)
    draft_message: str = ""


class RecalledMemory(APIModel):
    case_id: str | None = None
    memory_type: str
    text: str


class GuardrailResult(APIModel):
    passed: bool
    triggered: list[str] = Field(default_factory=list)
    downgraded_from: DecisionAction | None = None


class DecisionResponse(APIModel):
    invoice_id: str
    memory_used: bool
    exceptions: list[InvoiceException] = Field(default_factory=list)
    recalled: list[RecalledMemory] = Field(default_factory=list)
    decision: ProposedDecision
    guardrails: GuardrailResult
    final_status: str
    latency_ms: int = Field(ge=0)


class InvoiceListResponse(RootModel[list[InvoiceSummary]]):
    """Invoice summary array response."""
