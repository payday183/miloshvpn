import time

from shared.queue import recover_stale_jobs, requeue_ready_jobs

RETRY_QUEUE = "queue:vpn:retry"
MAIN_QUEUE = "queue:vpn:create"
PROCESSING_QUEUE = "queue:vpn:create:processing"


def main():
    print("Retry worker started...")

    while True:
        recovered = recover_stale_jobs(PROCESSING_QUEUE, MAIN_QUEUE)
        moved = requeue_ready_jobs(RETRY_QUEUE, MAIN_QUEUE)

        if recovered or moved:
            print(
                f"[RETRY] recovered={recovered} moved={moved}"
            )

        time.sleep(1)


if __name__ == "__main__":
    main()
