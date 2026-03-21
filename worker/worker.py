from __future__ import annotations

import json

from backend.api.routes import services


def run_cycle() -> dict[str, object]:
    expired_subscriptions = services.subscription_service.expire_overdue_subscriptions()
    revoked_access = []

    for subscription in expired_subscriptions:
        access = services.vpn_service.ensure_access_matches_subscription(subscription.user_id)
        if access is not None:
            revoked_access.append(
                {
                    "id": access.id,
                    "user_id": access.user_id,
                }
            )

    return {
        "expired_subscriptions": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "status": item.status,
            }
            for item in expired_subscriptions
        ],
        "revoked_access": revoked_access,
    }


def main() -> None:
    print(
        json.dumps(
            {
                "message": "MiloshVPN worker scaffold is ready.",
                "cycle": run_cycle(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
