"""Thin async wrappers over SQS, S3 and EventBridge Scheduler (LocalStack in dev)."""

import json
from functools import lru_cache

import aioboto3

from app.config import get_settings


@lru_cache
def _session() -> aioboto3.Session:
    s = get_settings()
    return aioboto3.Session(
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
        region_name=s.aws_region,
    )


def client(service: str):
    return _session().client(service, endpoint_url=get_settings().aws_endpoint_url)


_queue_urls: dict[str, str] = {}


async def queue_url(sqs, name: str) -> str:
    if name not in _queue_urls:
        _queue_urls[name] = (await sqs.get_queue_url(QueueName=name))["QueueUrl"]
    return _queue_urls[name]


async def queue_arn(name: str) -> str:
    async with client("sqs") as sqs:
        attrs = await sqs.get_queue_attributes(
            QueueUrl=await queue_url(sqs, name), AttributeNames=["QueueArn"]
        )
        return attrs["Attributes"]["QueueArn"]


async def send_messages(queue: str, bodies: list[dict]) -> None:
    async with client("sqs") as sqs:
        url = await queue_url(sqs, queue)
        for i in range(0, len(bodies), 10):
            batch = bodies[i : i + 10]
            await sqs.send_message_batch(
                QueueUrl=url,
                Entries=[{"Id": str(j), "MessageBody": json.dumps(b)} for j, b in enumerate(batch)],
            )


async def put_object(key: str, body: bytes, content_type: str = "application/json") -> str:
    s = get_settings()
    async with client("s3") as s3:
        await s3.put_object(Bucket=s.artifacts_bucket, Key=key, Body=body, ContentType=content_type)
    return f"s3://{s.artifacts_bucket}/{key}"


async def create_schedule(name: str, expression: str, payload: dict, timezone: str) -> str:
    s = get_settings()
    target_arn = await queue_arn(s.reminders_queue)
    async with client("scheduler") as sch:
        resp = await sch.create_schedule(
            Name=name,
            ScheduleExpression=expression,
            ScheduleExpressionTimezone=timezone,
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={"Arn": target_arn, "RoleArn": s.scheduler_role_arn, "Input": json.dumps(payload)},
        )
    return resp["ScheduleArn"]


async def delete_schedule(name: str) -> None:
    async with client("scheduler") as sch:
        await sch.delete_schedule(Name=name)
