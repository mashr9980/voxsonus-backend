# app/routes/payments.py
from fastapi import APIRouter, HTTPException, Depends, status, Request, BackgroundTasks
import asyncpg
import stripe
import json
from app.core.database import get_db_connection
from app.core.security import get_current_active_user
from app.core.config import settings
from app.models.order import OrderResponse, OrderStatus, PaymentStatus
from app.services.subtitle_processor import process_order
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = settings.STRIPE_API_KEY

@router.post("/create-checkout-session/{order_id}")
async def create_checkout_session(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Get order and check ownership
        order = await conn.fetchrow(
            "SELECT * FROM orders WHERE id = $1 AND user_id = $2", 
            order_id, current_user["id"]
        )
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check if order is already paid
        if order["payment_status"] == PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order is already paid"
            )
        
        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Subtitle Order #{order_id}",
                            "description": f"Subtitle generation for {order['total_duration'] // 60} minutes of video",
                        },
                        "unit_amount": int(order["total_amount"] * 100),  # Stripe uses cents
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=f"{settings.ALLOWED_ORIGINS[0]}/orders/{order_id}/success",
            cancel_url=f"{settings.ALLOWED_ORIGINS[0]}/orders/{order_id}/cancel",
            client_reference_id=str(order_id),
            customer_email=current_user["email"],
            metadata={
                "order_id": order_id,
                "user_id": current_user["id"]
            }
        )
        
        # Update order with payment intent ID
        await conn.execute(
            "UPDATE orders SET payment_intent_id = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            checkout_session.payment_intent, order_id
        )
        
        return {"checkout_url": checkout_session.url}
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating checkout session: {str(e)}"
        )

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle the event
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        
        # Get order ID from metadata
        order_id = session.get("metadata", {}).get("order_id")
        if not order_id:
            logger.error("Order ID not found in session metadata")
            return {"success": False}
        
        try:
            # Update order status
            await conn.execute("""
                UPDATE orders 
                SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP
                WHERE id = $3
            """, PaymentStatus.PAID, OrderStatus.PAID, int(order_id))
            
            # Start processing in background
            background_tasks.add_task(process_order, int(order_id))
            
            logger.info(f"Payment successful for order {order_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Error processing payment webhook: {e}")
            return {"success": False}
    
    # For other events, just acknowledge receipt
    return {"success": True}