from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VpnNode
from app.timeutils import utcnow


async def list_nodes(session: AsyncSession) -> list[VpnNode]:
    return list((await session.scalars(select(VpnNode).order_by(VpnNode.id))).all())


async def get_active_node(session: AsyncSession) -> VpnNode | None:
    return await session.scalar(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))


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
