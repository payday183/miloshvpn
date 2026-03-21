from shared.queue import (
    ack_job,
    acquire_lock,
    claim_job,
    get_job,
    release_lock,
    reschedule_job,
    schedule_retry,
)
from backend.api.routes import services

QUEUE = "queue:vpn:create"
PROCESSING_QUEUE = "queue:vpn:create:processing"
RETRY_QUEUE = "queue:vpn:retry"
DEAD_QUEUE = "queue:vpn:dead"
LOCK_RETRY_DELAY = 5


def main():
    print("VPN worker started...")

    while True:
        job_id = claim_job(QUEUE, PROCESSING_QUEUE, timeout=5)
        if not job_id:
            continue

        job = get_job(job_id)
        if not job:
            continue

        user_id = job["payload"]["user_id"]
        lock_key = f"lock:user:{user_id}"

        if not acquire_lock(lock_key):
            reschedule_job(
                job_id,
                processing_queue=PROCESSING_QUEUE,
                retry_queue=RETRY_QUEUE,
                delay=LOCK_RETRY_DELAY,
                status="pending",
            )
            continue

        try:
            access = services.vpn_service.provision_access(user_id)

            ack_job(
                job_id,
                processing_queue=PROCESSING_QUEUE,
                result={"access_id": access.id},
            )

            print(f"[OK] VPN created {user_id}")
        except Exception as e:
            print(f"[ERROR] {e}")

            schedule_retry(
                job_id,
                processing_queue=PROCESSING_QUEUE,
                retry_queue=RETRY_QUEUE,
                dead_queue=DEAD_QUEUE,
                error=str(e),
            )
        finally:
            release_lock(lock_key)


if __name__ == "__main__":
    main()
