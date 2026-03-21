import json
import os
import time
import uuid

from shared.redis import redis_client

JOB_TTL = int(os.getenv("JOB_TTL", "86400"))
PROCESSING_TIMEOUT = int(os.getenv("JOB_PROCESSING_TIMEOUT", "300"))
MAX_RETRIES = int(os.getenv("JOB_MAX_RETRIES", "3"))
RETRY_DELAYS = (15, 60, 300)


def _now() -> int:
    return int(time.time())


def create_job(queue: str, payload: dict) -> str:
    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "status": "pending",
        "payload": payload,
        "retries": 0,
        "queue": queue,
        "created_at": _now(),
    }

    redis_client.set(
        f"job:{job_id}",
        json.dumps(job),
        ex=JOB_TTL,
    )

    redis_client.lpush(queue, job_id)

    return job_id


def get_job(job_id: str):
    data = redis_client.get(f"job:{job_id}")
    return json.loads(data) if data else None


def update_job(job_id: str, **fields):
    job = get_job(job_id)
    if not job:
        return

    job.update(fields)

    redis_client.set(
        f"job:{job_id}",
        json.dumps(job),
        ex=JOB_TTL,
    )


def acquire_lock(key: str, ttl=30) -> bool:
    return redis_client.set(key, "1", nx=True, ex=ttl)


def release_lock(key: str):
    redis_client.delete(key)


def claim_job(queue: str, processing_queue: str, timeout: int = 5):
    job_id = redis_client.brpoplpush(queue, processing_queue, timeout=timeout)
    if not job_id:
        return None

    update_job(
        job_id,
        status="processing",
        processing_started_at=_now(),
    )
    return job_id


def ack_job(job_id: str, processing_queue: str, result: dict | None = None):
    redis_client.lrem(processing_queue, 1, job_id)
    update_job(
        job_id,
        status="done",
        result=result or {},
        processing_started_at=None,
        finished_at=_now(),
    )


def move_job_to_dead(job_id: str, processing_queue: str, dead_queue: str, error: str):
    redis_client.lrem(processing_queue, 1, job_id)
    redis_client.lpush(dead_queue, job_id)
    update_job(
        job_id,
        status="failed",
        error=error,
        processing_started_at=None,
        failed_at=_now(),
    )


def schedule_retry(
    job_id: str,
    processing_queue: str,
    retry_queue: str,
    error: str,
    dead_queue: str,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
    max_retries: int = MAX_RETRIES,
):
    job = get_job(job_id)
    if not job:
        redis_client.lrem(processing_queue, 1, job_id)
        return "missing"

    retries = int(job.get("retries", 0)) + 1
    if retries > max_retries:
        move_job_to_dead(job_id, processing_queue, dead_queue, error)
        return "dead"

    delay = retry_delays[min(retries - 1, len(retry_delays) - 1)]
    retry_at = _now() + delay

    redis_client.lrem(processing_queue, 1, job_id)
    redis_client.zadd(retry_queue, {job_id: retry_at})
    update_job(
        job_id,
        status="retry",
        retries=retries,
        error=error,
        retry_at=retry_at,
        processing_started_at=None,
    )
    return "retry"


def reschedule_job(
    job_id: str,
    processing_queue: str,
    retry_queue: str,
    delay: int,
    status: str = "pending",
):
    retry_at = _now() + delay
    redis_client.lrem(processing_queue, 1, job_id)
    redis_client.zadd(retry_queue, {job_id: retry_at})
    update_job(
        job_id,
        status=status,
        retry_at=retry_at,
        processing_started_at=None,
    )


def requeue_ready_jobs(retry_queue: str, main_queue: str, limit: int = 100) -> int:
    ready_job_ids = redis_client.zrangebyscore(retry_queue, 0, _now(), start=0, num=limit)
    moved = 0

    for job_id in ready_job_ids:
        removed = redis_client.zrem(retry_queue, job_id)
        if not removed:
            continue

        update_job(
            job_id,
            status="pending",
            retry_at=None,
            processing_started_at=None,
        )
        redis_client.lpush(main_queue, job_id)
        moved += 1

    return moved


def recover_stale_jobs(
    processing_queue: str,
    main_queue: str,
    stale_after: int = PROCESSING_TIMEOUT,
    limit: int = 100,
) -> int:
    now = _now()
    job_ids = redis_client.lrange(processing_queue, 0, max(limit - 1, 0))
    recovered = 0
    seen = set()

    for job_id in job_ids:
        if job_id in seen:
            continue
        seen.add(job_id)

        job = get_job(job_id)
        if not job:
            redis_client.lrem(processing_queue, 1, job_id)
            continue

        status = job.get("status")
        processing_started_at = job.get("processing_started_at")

        if status == "done":
            redis_client.lrem(processing_queue, 1, job_id)
            continue

        is_stale = (
            processing_started_at is None
            or int(processing_started_at) <= now - stale_after
        )
        if not is_stale:
            continue

        redis_client.lrem(processing_queue, 1, job_id)
        update_job(
            job_id,
            status="pending",
            processing_started_at=None,
            recovered_at=now,
        )
        redis_client.lpush(main_queue, job_id)
        recovered += 1

    return recovered
