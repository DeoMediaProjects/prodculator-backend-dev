"""Test script to verify webhook upgrade flow and cache busting."""
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import get_settings
from app.core.db import get_db
from app.core.database_client import DatabaseClient
from app.modules.payments.webhook_handler import WebhookHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_upgrade_flow(user_id: str, plan_type: str = "professional"):
    """Simulate a webhook checkout.session.completed event."""
    settings = get_settings()
    db = next(get_db())
    client = DatabaseClient(db, settings)
    
    # Check current state
    user_before = client.table("users").select("*").eq("id", user_id).single().execute()
    logger.info("BEFORE: User %s has plan=%s user_type=%s", 
                user_id, 
                user_before.data.get("plan") if user_before.data else None,
                user_before.data.get("user_type") if user_before.data else None)
    
    # Simulate webhook event
    fake_session = {
        "metadata": {
            "userId": user_id,
            "planType": plan_type,
        },
        "mode": "subscription",
        "subscription": f"sub_test_{user_id}",
        "customer": f"cus_test_{user_id}",
        "customer_details": {
            "address": {
                "country": "US",
                "state": "CA",
            }
        }
    }
    
    handler = WebhookHandler(client, settings)
    logger.info("Processing webhook for user %s...", user_id)
    handler._handle_checkout_completed(fake_session)
    
    # Check final state
    user_after = client.table("users").select("*").eq("id", user_id).single().execute()
    logger.info("AFTER: User %s has plan=%s user_type=%s", 
                user_id,
                user_after.data.get("plan") if user_after.data else None,
                user_after.data.get("user_type") if user_after.data else None)
    
    if user_after.data.get("plan") == plan_type:
        logger.info("✓ SUCCESS: User upgraded correctly!")
    else:
        logger.error("✗ FAILED: User NOT upgraded! Expected %s, got %s", 
                    plan_type, user_after.data.get("plan"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_webhook_upgrade.py <user_id> [plan_type]")
        print("Example: python test_webhook_upgrade.py 123e4567-e89b-12d3-a456-426614174000 professional")
        sys.exit(1)
    
    user_id = sys.argv[1]
    plan_type = sys.argv[2] if len(sys.argv) > 2 else "professional"
    
    test_upgrade_flow(user_id, plan_type)
