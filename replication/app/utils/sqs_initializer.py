"""Initialize SQS queues for diagrams-v2."""

import boto3

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def initialize_queues() -> bool:
    """Create SQS FIFO queues in LocalStack or AWS on startup.

    Uses FIFO queues in ALL environments (including LocalStack),
    matching the atonis-req-copilot pattern exactly.
    """
    logger.info("Initializing SQS queues...")

    try:
        if settings.local_environment:
            sqs_client = boto3.client(
                "sqs",
                region_name=settings.aws_region,
                aws_access_key_id=settings.aws_access_key_id or "test",
                aws_secret_access_key=settings.aws_secret_access_key or "test",
                endpoint_url=settings.localstack_url,
            )
            logger.info(f"Using LocalStack: {settings.localstack_url}")
        else:
            sqs_client = boto3.client("sqs", region_name=settings.aws_region)
            logger.info(f"Using AWS SQS in region: {settings.aws_region}")

        # Create request queue (FIFO)
        logger.info(f"Creating request queue: {settings.sqs_request_queue_name}")
        _ensure_queue(
            sqs_client,
            queue_name=settings.sqs_request_queue_name,
            attributes={
                "FifoQueue": "true",
                "ContentBasedDeduplication": "false",
                "MessageRetentionPeriod": "86400",
                "VisibilityTimeout": "3600",
            },
        )

        # Create response queue (FIFO)
        logger.info(f"Creating response queue: {settings.sqs_response_queue_name}")
        _ensure_queue(
            sqs_client,
            queue_name=settings.sqs_response_queue_name,
            attributes={
                "FifoQueue": "true",
                "ContentBasedDeduplication": "false",
                "MessageRetentionPeriod": "86400",
                "VisibilityTimeout": "60",
            },
        )

        logger.info(" SQS queue initialization complete")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize SQS queues: {e}")
        if settings.local_environment:
            logger.error("Make sure LocalStack is running:")
            logger.error("  docker run -d -p 4566:4566 localstack/localstack")
        return False


def _ensure_queue(sqs_client, queue_name: str, attributes: dict) -> str:
    """Create queue if it doesn't exist, return its URL."""
    try:
        response = sqs_client.create_queue(QueueName=queue_name, Attributes=attributes)
        url = response["QueueUrl"]
        logger.info(f"  ✓ Queue created: {queue_name} → {url}")
        return url
    except sqs_client.exceptions.QueueNameExists:
        url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
        logger.info(f"  ✓ Queue exists:  {queue_name} → {url}")
        return url
    except Exception as e:
        logger.warning(f"   Could not create/get queue {queue_name}: {e}")
        return ""
