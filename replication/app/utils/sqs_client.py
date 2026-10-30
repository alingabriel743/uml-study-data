"""SQS client utility for diagrams-v2."""

import boto3
from botocore.config import Config

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_sqs_client():
    """Get SQS client — fresh instance every call (no caching).

    Matches atonis-req-copilot pattern: a new boto3 client per call avoids
    connection pool contention between the worker's long-polling
    receive_message and the proxy's send_message on LocalStack.
    """
    if settings.local_environment:
        return boto3.client(
            "sqs",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or "test",
            aws_secret_access_key=settings.aws_secret_access_key or "test",
            endpoint_url=settings.localstack_url,
        )
    else:
        return boto3.client(
            "sqs",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )


# Queue URL cache — URLs never change once resolved, safe to cache.
# (Only clients are created fresh to avoid connection pool contention.)
_queue_url_cache: dict[str, str] = {}


def get_queue_url(queue_name: str) -> str:
    """Get the URL for a specific SQS queue (cached after first resolution).

    FIFO queues are used everywhere (including LocalStack),
    so the queue name is used as-is — no .fifo stripping.
    Queue URLs are cached because they never change during the app lifetime.
    """
    if queue_name in _queue_url_cache:
        return _queue_url_cache[queue_name]
    client = get_sqs_client()
    url = client.get_queue_url(QueueName=queue_name)["QueueUrl"]
    _queue_url_cache[queue_name] = url
    return url


def send_sqs_message(
    client,
    *,
    queue_url: str,
    message_body: str,
    message_group_id: str,
    message_deduplication_id: str,
) -> dict:
    """Send an SQS message with FIFO parameters.

    FIFO queues are used in all environments (including LocalStack),
    so MessageGroupId and MessageDeduplicationId are always included.
    """
    return client.send_message(
        QueueUrl=queue_url,
        MessageBody=message_body,
        MessageGroupId=message_group_id,
        MessageDeduplicationId=message_deduplication_id,
    )
