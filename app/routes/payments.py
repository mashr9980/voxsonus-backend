# app/routes/payments.py (Updated)
from fastapi import APIRouter, HTTPException, Depends, status, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import stripe
import asyncpg
import json
import logging
from app.core.database import get_db_connection, log_activity
from app.core.config import settings
from app.core.security import get_current_active_user
from app.models.payment import CheckoutSessionResponse, PaymentStatusResponse
from app.models.order import OrderStatus, PaymentStatus
from app.routes.admin import process_order_background
from app.services.subtitle_processor import process_order

router = APIRouter()
logger = logging.getLogger(__name__)

# Configure Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY

@router.post("/simulate-success/{order_id}")
async def simulate_payment_success(
    order_id: int,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    """Simulate successful payment for testing (without actual Stripe payment)"""
    try:
        # Get order details
        order = await conn.fetchrow("""
            SELECT * FROM orders 
            WHERE id = $1 AND user_id = $2
        """, order_id, current_user["id"])
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check if order can be paid
        if order["payment_status"] == PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order is already paid"
            )
        
        if order["status"] not in [OrderStatus.CREATED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order cannot be paid in current status"
            )
        
        # Simulate successful payment
        await conn.execute("""
            UPDATE orders 
            SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP,
                payment_intent_id = $3
            WHERE id = $4
        """, PaymentStatus.PAID, OrderStatus.PAID, f"simulated_payment_{order_id}", order_id)
        
        # Log activity
        await log_activity(
            conn, 
            current_user["id"], 
            "simulated_payment_success", 
            "orders", 
            order_id,
            {"amount": float(order["total_amount"]), "method": "simulation"}
        )
        
        # Start processing in background
        background_tasks.add_task(process_order_background, order_id)
        
        return {
            "message": "Payment simulated successfully",
            "order_id": order_id,
            "status": "paid",
            "processing_started": True
        }
        
    except Exception as e:
        logger.error(f"Error simulating payment success: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to simulate payment"
        )

@router.post("/create-checkout-session/{order_id}", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    try:
        # Get order details
        order = await conn.fetchrow("""
            SELECT * FROM orders 
            WHERE id = $1 AND user_id = $2
        """, order_id, current_user["id"])
        
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
        
        # Check if order is in correct status
        if order["status"] not in [OrderStatus.CREATED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order cannot be paid in current status"
            )
        
        # Create Stripe checkout session
        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'AI Subtitle Generation - Order #{order_id}',
                            'description': f'Subtitle generation for {order["total_duration"]} seconds of video',
                        },
                        'unit_amount': int(order["total_amount"] * 100),  # Convert to cents
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=settings.FRONTEND_SUCCESS_URL.format(order_id=order_id),
                cancel_url=settings.FRONTEND_CANCEL_URL.format(order_id=order_id),
                metadata={
                    'order_id': str(order_id),
                    'user_id': str(current_user["id"])
                }
            )
            
            # Update order with payment intent ID
            await conn.execute("""
                UPDATE orders 
                SET payment_intent_id = $1, updated_at = CURRENT_TIMESTAMP
                WHERE id = $2
            """, checkout_session.id, order_id)
            
            # Log activity
            await log_activity(
                conn, 
                current_user["id"], 
                "create_checkout_session", 
                "orders", 
                order_id,
                {"session_id": checkout_session.id, "amount": order["total_amount"]}
            )
            
            return {
                "success": True,
                "checkout_url": checkout_session.url,
                "session_id": checkout_session.id
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create payment session"
            )
    
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Handle Stripe webhooks"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        # Verify webhook signature
        if settings.STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        else:
            # For development - parse without verification
            event = stripe.Event.construct_from(
                json.loads(payload.decode('utf-8')), stripe.api_key
            )
        
        logger.info(f"Received Stripe webhook: {event['type']}")
        
        # Handle the event
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            await handle_payment_success(session, conn, background_tasks)
            
        elif event['type'] == 'checkout.session.expired':
            session = event['data']['object']
            await handle_payment_expired(session, conn)
            
        elif event['type'] == 'payment_intent.payment_failed':
            payment_intent = event['data']['object']
            await handle_payment_failed(payment_intent, conn)
        
        else:
            logger.info(f"Unhandled webhook event type: {event['type']}")
        
        return JSONResponse(content={"status": "success"})
    
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")

# app/routes/payments.py - Updated handle_payment_success function

async def handle_payment_success(session, conn, background_tasks):
    """Handle successful payment"""
    try:
        order_id = int(session['metadata']['order_id'])
        user_id = int(session['metadata']['user_id'])
        
        # Update order status to PAID first, then will be changed to PROCESSING by background task
        await conn.execute("""
            UPDATE orders 
            SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP
            WHERE id = $3 AND user_id = $4
        """, PaymentStatus.PAID, OrderStatus.PAID, order_id, user_id)
        
        # Update all videos in this order to processing status
        await conn.execute("""
            UPDATE videos 
            SET status = $1, updated_at = CURRENT_TIMESTAMP
            WHERE order_id = $2
        """, "processing", order_id)
        
        # Log activity
        await log_activity(
            conn, 
            user_id, 
            "payment_success", 
            "orders", 
            order_id,
            {
                "session_id": session['id'], 
                "amount_paid": session['amount_total'] / 100,
                "payment_method": session.get('payment_method_types', [])
            }
        )
        
        # Start subtitle processing in background
        background_tasks.add_task(process_order, order_id)
        
        logger.info(f"Payment successful for order {order_id}, processing started")
        
    except Exception as e:
        logger.error(f"Error handling payment success: {e}")

async def handle_payment_expired(session, conn):
    """Handle expired payment session"""
    try:
        order_id = int(session['metadata']['order_id'])
        user_id = int(session['metadata']['user_id'])
        
        # Log activity
        await log_activity(
            conn, 
            user_id, 
            "payment_expired", 
            "orders", 
            order_id,
            {"session_id": session['id']}
        )
        
        logger.info(f"Payment session expired for order {order_id}")
        
    except Exception as e:
        logger.error(f"Error handling payment expiration: {e}")

async def handle_payment_failed(payment_intent, conn):
    """Handle failed payment"""
    try:
        # You might need to look up the order by payment_intent_id
        # This depends on how you store the relationship
        logger.info(f"Payment failed for intent {payment_intent['id']}")
        
    except Exception as e:
        logger.error(f"Error handling payment failure: {e}")

@router.get("/status/{order_id}", response_model=PaymentStatusResponse)
async def check_payment_status(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    """Check payment status for an order"""
    try:
        # Get order details
        order = await conn.fetchrow("""
            SELECT id, status, payment_status, payment_intent_id, total_amount, updated_at
            FROM orders 
            WHERE id = $1 AND user_id = $2
        """, order_id, current_user["id"])
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        result = {
            "order_id": order["id"],
            "order_status": order["status"],
            "payment_status": order["payment_status"],
            "total_amount": float(order["total_amount"]),
            "last_updated": order["updated_at"]
        }
        
        # If we have a Stripe session ID, check Stripe status as well
        if order["payment_intent_id"]:
            try:
                # Check if it's a checkout session ID or payment intent ID
                if order["payment_intent_id"].startswith("cs_"):
                    # It's a checkout session
                    session = stripe.checkout.Session.retrieve(order["payment_intent_id"])
                    result["stripe_status"] = session.payment_status
                    result["stripe_session_status"] = session.status
                else:
                    # It's a payment intent
                    payment_intent = stripe.PaymentIntent.retrieve(order["payment_intent_id"])
                    result["stripe_status"] = payment_intent.status
                    
            except stripe.error.StripeError as e:
                logger.warning(f"Could not retrieve Stripe status: {e}")
                result["stripe_status"] = "unknown"
        
        return result
        
    except Exception as e:
        logger.error(f"Error checking payment status: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check payment status"
        )

@router.post("/manual-verify/{order_id}")
async def manually_verify_payment(
    order_id: int,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db_connection),
    current_user: dict = Depends(get_current_active_user)
):
    """Manually verify payment status with Stripe (useful for development)"""
    try:
        # Get order details
        order = await conn.fetchrow("""
            SELECT * FROM orders 
            WHERE id = $1 AND user_id = $2
        """, order_id, current_user["id"])
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        if not order["payment_intent_id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No payment session found for this order"
            )
        
        # Check Stripe session status
        try:
            session = stripe.checkout.Session.retrieve(order["payment_intent_id"])
            
            if session.payment_status == "paid":
                # Update order status if not already updated
                if order["payment_status"] != PaymentStatus.PAID:
                    await conn.execute("""
                        UPDATE orders 
                        SET payment_status = $1, status = $2, updated_at = CURRENT_TIMESTAMP
                        WHERE id = $3
                    """, PaymentStatus.PAID, OrderStatus.PAID, order_id)
                    
                    # Start processing
                    background_tasks.add_task(process_order, order_id)
                    
                    # Log activity
                    await log_activity(
                        conn, 
                        current_user["id"], 
                        "manual_payment_verification", 
                        "orders", 
                        order_id,
                        {"session_id": session.id, "verified_status": "paid"}
                    )
                
                return {"message": "Payment verified and order updated", "status": "paid"}
            else:
                return {"message": "Payment not completed", "status": session.payment_status}
                
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error during manual verification: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify payment with Stripe"
            )
    
    except Exception as e:
        logger.error(f"Error in manual payment verification: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment"
        )