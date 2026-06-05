from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VpnKey, VpnNode
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def refresh_node_status(session: AsyncSession, node: VpnNode) -> VpnNode:
    snapshot = await X3UIClient(node=node).collect_snapshot()
    node.status = snapshot.status
    node.last_checked_at = utcnow()
    node.last_latency_ms = snapshot.latency_ms
    node.last_error = snapshot.error
    node.remote_clients = snapshot.remote_clients
    node.remote_enabled_clients = snapshot.remote_enabled_clients
    node.traffic_up_bytes = snapshot.traffic_up_bytes
    node.traffic_down_bytes = snapshot.traffic_down_bytes
    node.cpu_percent = snapshot.cpu_percent
    node.memory_percent = snapshot.memory_percent
    node.disk_percent = snapshot.disk_percent
    node.updated_at = utcnow()
    await session.flush()
    return node


async def refresh_all_nodes(session: AsyncSession) -> list[VpnNode]:
    nodes = (await session.scalars(select(VpnNode).order_by(VpnNode.id))).all()
    for node in nodes:
        await refresh_node_status(session, node)
    await session.commit()
    return list(nodes)


async def local_key_counts(session: AsyncSession, node_id: int) -> dict[str, int]:
    total = await session.scalar(select(func.count()).select_from(VpnKey).where(VpnKey.node_id == node_id))
    active = await session.scalar(
        select(func.count()).select_from(VpnKey).where(VpnKey.node_id == node_id, VpnKey.active.is_(True))
    )
    private_active = await session.scalar(
        select(func.count())
        .select_from(VpnKey)
        .where(VpnKey.node_id == node_id, VpnKey.active.is_(True), VpnKey.key_type == "private")
    )
    public_active = await session.scalar(
        select(func.count())
        .select_from(VpnKey)
        .where(VpnKey.node_id == node_id, VpnKey.active.is_(True), VpnKey.key_type == "public")
    )
    return {
        "total": int(total or 0),
        "active": int(active or 0),
        "private_active": int(private_active or 0),
        "public_active": int(public_active or 0),
    }


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
