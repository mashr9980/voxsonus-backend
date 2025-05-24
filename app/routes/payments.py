# app/routes/payments.py
from fastapi import APIRouter, HTTPException, Depends, status, Request, BackgroundTasks, Query
import asyncpg
import stripe
import json
from app.core.database import get_db_connection, log_activity
from app.core.security import get_current_active_user
from app.core.config import settings
from app.models.order import OrderResponse, OrderStatus, PaymentStatus
from app.models.payment import CheckoutSessionResponse, PaymentStatusResponse
from app.services.subtitle_processor import process_order
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = settings.STRIPE_API_KEY

@router.post("/create-checkout-session/{order_id}", response_model=CheckoutSessionResponse)
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
            success_url=f"{settings.FRONTEND_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&order_id={order_id}",
            cancel_url=f"{settings.FRONTEND_URL}/payment/cancel?order_id={order_id}",
            client_reference_id=str(order_id),
            customer_email=current_user["email"],
            metadata={
                "order_id": order_id,
                "user_id": current_user["id"]
            }
        )
        
        # Update order with session ID
        await conn.execute(
            "UPDATE orders SET payment_intent_id = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            checkout_session.id, order_id
        )
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "create_checkout_session", 
            "orders", 
            order_id,
            {"session_id": checkout_session.id}
        )
        
        return {
            "success": True,
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id
        }
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating checkout session: {str(e)}"
        )

@router.get("/success")
async def payment_success(
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection),
    session_id: str = Query(...),
    order_id: int = Query(...)
):
    """Handle successful payment redirect from Stripe"""
    try:
        # Verify the session with Stripe
        session = stripe.checkout.Session.retrieve(session_id)
        
        if session.payment_status != "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment not completed"
            )
        
        # Get order
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check if order is already processed
        if order["payment_status"] == PaymentStatus.PAID:
            return {
                "success": True,
                "message": "Payment already processed",
                "order_id": order_id,
                "status": order["status"]
            }
        
        # Update order status
        await conn.execute("""
            UPDATE orders 
            SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP
            WHERE id = $3
        """, PaymentStatus.PAID, OrderStatus.PAID, order_id)
        
        # Log activity
        await log_activity(
            conn, 
            order["user_id"], 
            "payment_success", 
            "orders", 
            order_id,
            {"session_id": session_id, "amount": order["total_amount"]}
        )
        
        # Start processing in background
        background_tasks.add_task(process_order, order_id)
        
        logger.info(f"Payment successful for order {order_id}")
        
        return {
            "success": True,
            "message": "Payment successful! Your order is now being processed.",
            "order_id": order_id,
            "status": OrderStatus.PAID
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in success handler: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment verification failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error in payment success handler: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing successful payment"
        )

@router.get("/cancel")
async def payment_cancel(
    conn: asyncpg.Connection = Depends(get_db_connection),
    order_id: int = Query(...)
):
    """Handle cancelled payment redirect from Stripe"""
    try:
        # Get order
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Log activity
        await log_activity(
            conn, 
            order["user_id"], 
            "payment_cancelled", 
            "orders", 
            order_id,
            None
        )
        
        logger.info(f"Payment cancelled for order {order_id}")
        
        return {
            "success": False,
            "message": "Payment was cancelled. You can try again later.",
            "order_id": order_id,
            "status": order["status"]
        }
        
    except Exception as e:
        logger.error(f"Error in payment cancel handler: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing cancelled payment"
        )

@router.get("/status/{order_id}", response_model=PaymentStatusResponse)
async def get_payment_status(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    """Get payment status for an order"""
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
        
        # Get Stripe session info if available
        stripe_status = None
        stripe_session_status = None
        
        if order["payment_intent_id"]:
            try:
                # Check if it's a session ID or payment intent ID
                if order["payment_intent_id"].startswith("cs_"):
                    session = stripe.checkout.Session.retrieve(order["payment_intent_id"])
                    stripe_status = session.payment_status
                    stripe_session_status = session.status
                else:
                    # It's a payment intent ID
                    payment_intent = stripe.PaymentIntent.retrieve(order["payment_intent_id"])
                    stripe_status = payment_intent.status
            except stripe.error.StripeError as e:
                logger.warning(f"Could not retrieve Stripe payment info: {e}")
        
        return {
            "order_id": order_id,
            "order_status": order["status"],
            "payment_status": order["payment_status"],
            "total_amount": float(order["total_amount"]),
            "last_updated": order["updated_at"],
            "stripe_status": stripe_status,
            "stripe_session_status": stripe_session_status
        }
        
    except Exception as e:
        logger.error(f"Error getting payment status: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving payment status"
        )

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Handle Stripe webhooks for payment events"""
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
            # Try to get from client_reference_id as fallback
            order_id = session.get("client_reference_id")
        
        if not order_id:
            logger.error("Order ID not found in session metadata or client_reference_id")
            return {"success": False}
        
        try:
            order_id = int(order_id)
            
            # Check if order is already processed
            existing_order = await conn.fetchrow(
                "SELECT payment_status FROM orders WHERE id = $1", order_id
            )
            
            if not existing_order:
                logger.error(f"Order {order_id} not found in webhook handler")
                return {"success": False}
            
            if existing_order["payment_status"] == PaymentStatus.PAID:
                logger.info(f"Order {order_id} already processed, skipping webhook")
                return {"success": True}
            
            # Update order status
            await conn.execute("""
                UPDATE orders 
                SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP
                WHERE id = $3
            """, PaymentStatus.PAID, OrderStatus.PAID, order_id)
            
            # Get user ID for logging
            user_id = session.get("metadata", {}).get("user_id")
            if user_id:
                await log_activity(
                    conn, 
                    int(user_id), 
                    "payment_webhook_success", 
                    "orders", 
                    order_id,
                    {"session_id": session["id"]}
                )
            
            # Start processing in background
            background_tasks.add_task(process_order, order_id)
            
            logger.info(f"Webhook: Payment successful for order {order_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Error processing payment webhook: {e}")
            return {"success": False}
    
    elif event["type"] == "checkout.session.expired":
        session = event["data"]["object"]
        order_id = session.get("metadata", {}).get("order_id") or session.get("client_reference_id")
        
        if order_id:
            try:
                order_id = int(order_id)
                
                # Log the expiration
                user_id = session.get("metadata", {}).get("user_id")
                if user_id:
                    await log_activity(
                        conn, 
                        int(user_id), 
                        "payment_session_expired", 
                        "orders", 
                        order_id,
                        {"session_id": session["id"]}
                    )
                
                logger.info(f"Webhook: Payment session expired for order {order_id}")
            except Exception as e:
                logger.error(f"Error processing session expiration webhook: {e}")
    
    elif event["type"] == "payment_intent.payment_failed":
        payment_intent = event["data"]["object"]
        
        # Try to find the order by payment intent ID
        order = await conn.fetchrow(
            "SELECT id, user_id FROM orders WHERE payment_intent_id = $1",
            payment_intent["id"]
        )
        
        if order:
            await log_activity(
                conn, 
                order["user_id"], 
                "payment_failed", 
                "orders", 
                order["id"],
                {"payment_intent_id": payment_intent["id"], "failure_reason": payment_intent.get("last_payment_error", {}).get("message")}
            )
            
            logger.info(f"Webhook: Payment failed for order {order['id']}")
    
    # For other events, just acknowledge receipt
    return {"success": True}