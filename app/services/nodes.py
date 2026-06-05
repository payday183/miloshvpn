from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import VpnNode
from app.services.node_monitor import local_key_counts
from app.timeutils import utcnow


async def list_nodes(session: AsyncSession) -> list[VpnNode]:
    return list((await session.scalars(select(VpnNode).order_by(VpnNode.id))).all())


async def get_active_node(session: AsyncSession) -> VpnNode | None:
    return await session.scalar(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))


async def select_node_for_key(session: AsyncSession) -> VpnNode | None:
    settings = get_settings()
    if settings.node_selection_mode == "active":
        return await get_active_node(session)

    nodes = (
        await session.scalars(
            select(VpnNode)
            .where(VpnNode.is_active.is_(True))
            .order_by(VpnNode.id)
        )
    ).all()
    if not nodes:
        return None

    candidates: list[tuple[float, VpnNode]] = []
    for node in nodes:
        counts = await local_key_counts(session, node.id)
        active_keys = counts["private_active"]
        free_slots = max(node.max_clients - active_keys, 0)
        if free_slots <= 0:
            continue
        if node.status == "offline":
            continue
        if is_overloaded(node):
            continue

        load_ratio = active_keys / max(node.max_clients, 1)
        remote_ratio = node.remote_enabled_clients / max(node.max_clients, 1)
        cpu = (node.cpu_percent or 0) / 100
        memory = (node.memory_percent or 0) / 100
        disk = (node.disk_percent or 0) / 100
        latency = min(node.last_latency_ms or 0, 5000) / 5000
        unknown_penalty = 0.15 if node.status == "unknown" else 0
        score = (
            load_ratio * 0.35
            + remote_ratio * 0.2
            + cpu * 0.15
            + memory * 0.15
            + disk * 0.05
            + latency * 0.05
            + unknown_penalty
        )
        candidates.append((score, node))

    if not candidates:
        return await get_active_node(session)

    return min(candidates, key=lambda item: (item[0], item[1].id))[1]


def is_overloaded(node: VpnNode) -> bool:
    settings = get_settings()
    return any(
        [
            node.cpu_percent is not None and node.cpu_percent >= settings.node_overload_cpu_percent,
            node.memory_percent is not None and node.memory_percent >= settings.node_overload_memory_percent,
            node.disk_percent is not None and node.disk_percent >= settings.node_overload_disk_percent,
        ]
    )


async def create_node(
    session: AsyncSession,
    *,
    title: str,
    mode: str,
    base_url: str,
    username: str,
    password: str,
    inbound_id: int,
    max_clients: int,
    public_host: str,
    public_port: int,
    vless_query: str,
    activate: bool,
) -> VpnNode:
    now = utcnow()
    if activate:
        await deactivate_all_nodes(session)

    node = VpnNode(
        title=title.strip(),
        mode=mode,
        base_url=base_url.strip().rstrip("/"),
        username=username.strip(),
        password=password,
        inbound_id=inbound_id,
        max_clients=max_clients,
        public_host=public_host.strip(),
        public_port=public_port,
        vless_query=vless_query.strip(),
        is_active=activate,
        status="unknown",
        created_at=now,
        updated_at=now,
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def activate_node(session: AsyncSession, node_id: int) -> VpnNode:
    node = await session.get(VpnNode, node_id)
    if node is None:
        raise ValueError("Node not found")

    await deactivate_all_nodes(session)
    node.is_active = True
    node.updated_at = utcnow()
    await session.commit()
    await session.refresh(node)
    return node


async def disable_node(session: AsyncSession, node_id: int) -> VpnNode:
    node = await session.get(VpnNode, node_id)
    if node is None:
        raise ValueError("Node not found")

    node.is_active = False
    node.updated_at = utcnow()
    await session.commit()
    await session.refresh(node)
    return node


async def deactivate_all_nodes(session: AsyncSession) -> None:
    nodes = (await session.scalars(select(VpnNode).where(VpnNode.is_active.is_(True)))).all()
    now = utcnow()
    for node in nodes:
        node.is_active = False
        node.updated_at = now
