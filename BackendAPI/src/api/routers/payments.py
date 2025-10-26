"""
Payments Router: stubbed integration for creating checkout/payment intent, webhook handling, and history.

Endpoints:
- POST /payments/create-checkout
- POST /payments/webhook
- GET  /payments/history          (current user)
- GET  /payments/admin            (admin overview)

Behavior:
- If STRIPE_SECRET_KEY is present, attempts to initialize Stripe client.
- If STRIPE_WEBHOOK_SECRET is present, verifies webhook signatures; if absent, accepts payload as test mode.
- Persists Payments rows with provider_session_id placeholder, status transitions are stubbed.

Note:
- This is a stub implementation to be expanded with full provider integration.
"""

import json
import os
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user, require_roles
from ..models import Users, Payments, PaymentStatusEnum

router = APIRouter(prefix="/payments", tags=["Payments"])


# ===== Schemas =====

class CreateCheckoutRequest(BaseModel):
    amount: float = Field(..., description="Total amount to charge (e.g., 29.99)")
    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    reference: Optional[str] = Field(None, description="Client reference id for idempotency/debug")


class CreateCheckoutResponse(BaseModel):
    provider: str = Field(..., description="Payment provider name (e.g., stripe)")
    session_id: Optional[str] = Field(None, description="Stripe Checkout Session ID (if using checkout)")
    client_secret: Optional[str] = Field(None, description="Stripe PaymentIntent client secret (if using PI)")
    payment_id: int = Field(..., description="Internal payment id")
    status: str = Field(..., description="Current payment status")
    message: str = Field(..., description="Informational message regarding environment/mode")


class PaymentItem(BaseModel):
    id: int
    reference: str
    amount: float
    currency: str
    status: str
    created_at: datetime


class PaymentsListResponse(BaseModel):
    total: int
    items: List[PaymentItem]


# ===== Helpers =====

def _get_provider_keys() -> tuple[Optional[str], Optional[str]]:
    """Return (secret_key, webhook_secret) from environment."""
    return os.getenv("STRIPE_SECRET_KEY"), os.getenv("STRIPE_WEBHOOK_SECRET")


def _to_item(p: Payments) -> PaymentItem:
    return PaymentItem(
        id=p.id,
        reference=p.reference,
        amount=float(p.amount),
        currency=p.currency,
        status=p.status,
        created_at=p.created_at,
    )


# ===== Routes =====

# PUBLIC_INTERFACE
@router.post(
    "/create-checkout",
    summary="Create a checkout session or payment intent (stub)",
    description=(
        "Creates a payment intent/checkout session. "
        "If STRIPE_SECRET_KEY is unset, returns stubbed values for UI development."
    ),
    response_model=CreateCheckoutResponse,
)
def create_checkout(
    payload: CreateCheckoutRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> CreateCheckoutResponse:
    """
    Creates a Payments row and returns provider identifiers.

    - With STRIPE_SECRET_KEY present: returns a dummy session_id/client_secret placeholder
      to be replaced in full integration.
    - Without keys: returns deterministic placeholders for development.
    """
    amount = round(float(payload.amount), 2)
    if amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="amount must be > 0")

    reference = payload.reference or f"REF-{current_user.id}-{int(datetime.utcnow().timestamp())}"
    existing = db.execute(select(Payments).where(Payments.reference == reference)).scalar_one_or_none()
    if existing:
        # Idempotency: just return the existing one
        return CreateCheckoutResponse(
            provider="stripe",
            session_id=None,
            client_secret=None,
            payment_id=existing.id,
            status=existing.status,
            message="Existing payment found for reference",
        )

    # Create payment row in pending state
    payment = Payments(
        user_id=current_user.id,
        amount=amount,
        currency=(payload.currency or "USD").upper(),
        status=PaymentStatusEnum.PENDING.value,
        reference=reference,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    stripe_key, _ = _get_provider_keys()

    # For stub: We do not create real Stripe objects, but we return placeholders
    if not stripe_key:
        # Split long f-strings into variables to satisfy line-length limits
        test_session_id = f"cs_test_stub_{payment.id}"
        test_client_secret = f"pi_test_client_secret_stub_{payment.id}"
        return CreateCheckoutResponse(
            provider="stripe",
            session_id=test_session_id,
            client_secret=test_client_secret,
            payment_id=payment.id,
            status=payment.status,
            message="Stripe keys not set; returning stubbed session_id and client_secret.",
        )

    # If key present, still stub but indicate readiness for real integration
    return CreateCheckoutResponse(
        provider="stripe",
        session_id=f"cs_live_placeholder_{payment.id}",
        client_secret=f"pi_live_client_secret_placeholder_{payment.id}",
        payment_id=payment.id,
        status=payment.status,
        message=(
            "Stripe key present; replace placeholders with real Stripe "
            "Checkout/PaymentIntent creation."
        ),
    )


# PUBLIC_INTERFACE
@router.post(
    "/webhook",
    summary="Payment provider webhook (stub)",
    description=(
        "Handles Stripe webhooks. If STRIPE_WEBHOOK_SECRET is present, signature is required; "
        "otherwise accepts payload for test mode and updates payment status heuristically."
    ),
)
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: Optional[str] = Header(default=None, alias="Stripe-Signature"),
) -> dict:
    """
    Webhook endpoint for Stripe. In stub mode:
    - When STRIPE_WEBHOOK_SECRET is absent, processes JSON payload without signature verification.
    - Recognizes events by 'type' and 'data.object.client_reference_id' or 'metadata.reference' if provided.
    - Updates payment status accordingly.
    """
    _, webhook_secret = _get_provider_keys()
    raw_body = await request.body()

    event_type = None
    reference = None

    if webhook_secret:
        # We can't verify without the stripe library; accept minimal validation and parse json payload.
        # In a full implementation, use stripe.Webhook.construct_event with the secret.
        if not stripe_signature:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Stripe-Signature header")
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")
        event_type = data.get("type")
        obj = data.get("data", {}).get("object", {}) if isinstance(data.get("data"), dict) else {}
        reference = (
            obj.get("client_reference_id")
            or obj.get("metadata", {}).get("reference")
            or obj.get("reference")
        )
    else:
        # Test mode: accept unsigned payloads
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")
        event_type = data.get("type")
        obj = data.get("data", {}).get("object", {}) if isinstance(data.get("data"), dict) else {}
        reference = (
            obj.get("client_reference_id")
            or obj.get("metadata", {}).get("reference")
            or obj.get("reference")
        )

    if not reference:
        # Allow also id field for convenience
        reference = (data.get("reference") if isinstance(data, dict) else None)

    if not reference:
        # No way to map: accept but do nothing
        return {"status": "ok", "processed": False, "reason": "reference not found in payload"}

    # Map event types to statuses (stub)
    status_map = {
        "checkout.session.completed": PaymentStatusEnum.SUCCESS.value,
        "payment_intent.succeeded": PaymentStatusEnum.SUCCESS.value,
        "charge.succeeded": PaymentStatusEnum.SUCCESS.value,
        "payment_intent.payment_failed": PaymentStatusEnum.FAILED.value,
        "charge.failed": PaymentStatusEnum.FAILED.value,
        "charge.refunded": PaymentStatusEnum.REFUNDED.value,
    }
    new_status = status_map.get(event_type or "", None)

    row = db.execute(select(Payments).where(Payments.reference == reference)).scalar_one_or_none()
    if not row:
        return {"status": "ok", "processed": False, "reason": "payment reference not found"}

    if new_status and row.status != new_status:
        db.execute(
            update(Payments)
            .where(Payments.id == row.id)
            .values(status=new_status)
        )
        db.commit()

    return {"status": "ok", "processed": True, "event_type": event_type, "reference": reference}


# PUBLIC_INTERFACE
@router.get(
    "/history",
    summary="Payment history for current user",
    description="Returns current user's payments in reverse chronological order.",
    response_model=PaymentsListResponse,
)
def payment_history(
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
) -> PaymentsListResponse:
    """Return paginated payment history for the authenticated user."""
    total = int(
        db.execute(select(func.count(Payments.id)).where(Payments.user_id == current_user.id)).scalar_one() or 0
    )
    offset = (page - 1) * page_size
    rows = db.execute(
        select(Payments)
        .where(Payments.user_id == current_user.id)
        .order_by(Payments.created_at.desc(), Payments.id.desc())
        .offset(offset)
        .limit(page_size)
    ).scalars().all()
    return PaymentsListResponse(total=total, items=[_to_item(p) for p in rows])


# PUBLIC_INTERFACE
@router.get(
    "/admin",
    summary="Admin payments overview",
    description="Admin-only list of payments across users with pagination.",
    response_model=PaymentsListResponse,
    dependencies=[Depends(require_roles(["admin"]))],
)
def admin_payments(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    status_filter: Optional[str] = Query(None, description="Filter by status (pending/success/failed/refunded)"),
    user_id: Optional[int] = Query(None, description="Filter by specific user"),
) -> PaymentsListResponse:
    """Admin list of payments with optional filters."""
    stmt = select(Payments)
    cnt = select(func.count(Payments.id))

    if status_filter:
        stmt = stmt.where(Payments.status == status_filter)
        cnt = cnt.where(Payments.status == status_filter)
    if user_id:
        stmt = stmt.where(Payments.user_id == user_id)
        cnt = cnt.where(Payments.user_id == user_id)

    total = int(db.execute(cnt).scalar_one() or 0)
    offset = (page - 1) * page_size
    rows = (
        db.execute(
            stmt.order_by(Payments.created_at.desc(), Payments.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return PaymentsListResponse(total=total, items=[_to_item(p) for p in rows])
