from dataclasses import dataclass
from typing import Any, Literal

from app.config import get_settings
from app.services.x3ui import X3UIClient, X3UIError

InboundPurpose = Literal["user", "public", "admin", "admin_reality"]


@dataclass(frozen=True)
class InboundSelection:
    inbound_ids: tuple[int, ...]
    title: str


async def select_inbounds_for_client(purpose: InboundPurpose) -> InboundSelection:
    settings = get_settings()
    if settings.x3ui_mode == "mock":
        inbound_id = settings.admin_reality_inbound_id if purpose == "admin_reality" else settings.x3ui_inbound_id
        return InboundSelection((inbound_id,), "mock 3x-ui")

    x3ui = X3UIClient(settings)
    inbounds = await x3ui.list_inbounds()
    nodes = await x3ui.list_nodes()

    configured_ids = _configured_inbound_ids(purpose)
    if configured_ids:
        selected = _filter_inbounds_by_ids(inbounds, configured_ids)
        if not selected:
            raise X3UIError(f"Configured 3x-ui inbound ids were not found for {purpose}: {configured_ids}")
        return InboundSelection(tuple(_inbound_id(item) for item in selected), _selection_title(purpose, selected, nodes))

    enabled = [item for item in inbounds if _inbound_enabled(item)]
    if purpose == "admin_reality" and settings.admin_reality_inbound_id:
        selected = _filter_inbounds_by_ids(enabled, [settings.admin_reality_inbound_id])
        if not selected:
            raise X3UIError(f"Admin Reality inbound {settings.admin_reality_inbound_id} was not found")
        return InboundSelection(tuple(_inbound_id(item) for item in selected), _selection_title(purpose, selected, nodes))

    if purpose == "admin":
        selected = [item for item in enabled if _inbound_node_id(item) is None]
        if not selected:
            raise X3UIError("No local system 3x-ui inbounds are available for admin keys")
        return InboundSelection(tuple(_inbound_id(item) for item in selected), _selection_title(purpose, selected, nodes))

    if purpose == "public" and settings.x3ui_public_inbound_ids:
        selected = _filter_inbounds_by_ids(enabled, settings.x3ui_public_inbound_ids)
        if not selected:
            raise X3UIError("Configured public 3x-ui inbounds were not found")
        return InboundSelection(tuple(_inbound_id(item) for item in selected), _selection_title(purpose, selected, nodes))

    selected = _least_loaded_node_inbounds(enabled, nodes)
    if not selected:
        raise X3UIError("No remote node inbounds are available for user keys")
    return InboundSelection(tuple(_inbound_id(item) for item in selected), _selection_title(purpose, selected, nodes))


async def collect_system_x3ui_state() -> dict[str, Any]:
    return await X3UIClient(get_settings()).collect_system_state()


def _configured_inbound_ids(purpose: InboundPurpose) -> list[int]:
    settings = get_settings()
    if purpose == "admin":
        return settings.x3ui_admin_inbound_ids
    if purpose == "public":
        return settings.x3ui_public_inbound_ids
    if purpose == "user":
        return settings.x3ui_user_inbound_ids
    return []


def _filter_inbounds_by_ids(inbounds: list[dict[str, Any]], inbound_ids: list[int]) -> list[dict[str, Any]]:
    wanted = set(int(item) for item in inbound_ids)
    return [item for item in inbounds if _inbound_id(item) in wanted and _inbound_enabled(item)]


def _least_loaded_node_inbounds(
    inbounds: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    node_inbounds: dict[int, list[dict[str, Any]]] = {}
    for inbound in inbounds:
        node_id = _inbound_node_id(inbound)
        if node_id is None:
            continue
        node_inbounds.setdefault(node_id, []).append(inbound)

    if not node_inbounds:
        return []

    node_by_id = {_node_id(node): node for node in nodes if _node_id(node) is not None}
    candidates: list[tuple[float, int]] = []
    for node_id, items in node_inbounds.items():
        node = node_by_id.get(node_id, {})
        if not _node_enabled(node):
            continue
        score = _node_score(node, items)
        candidates.append((score, node_id))

    if not candidates:
        return []

    _, selected_node_id = min(candidates, key=lambda item: (item[0], item[1]))
    return node_inbounds[selected_node_id]


def _node_score(node: dict[str, Any], inbounds: list[dict[str, Any]]) -> float:
    client_count = _float_value(node.get("clientCount"))
    online_count = _float_value(node.get("onlineCount"))
    cpu = _float_value(node.get("cpuPct"))
    memory = _float_value(node.get("memPct"))
    latency = min(_float_value(node.get("latencyMs")), 5000.0) / 50.0
    inbound_clients = sum(len(item.get("clientStats") or []) for item in inbounds)
    offline_penalty = 10000.0 if str(node.get("status") or "").lower() not in {"", "online"} else 0.0
    return client_count + inbound_clients + online_count * 2 + cpu + memory + latency + offline_penalty


def _selection_title(purpose: InboundPurpose, inbounds: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> str:
    if not inbounds:
        return purpose
    node_id = _inbound_node_id(inbounds[0])
    node = next((item for item in nodes if _node_id(item) == node_id), None)
    if node is not None:
        name = str(node.get("name") or node.get("remark") or f"node {node_id}")
        return f"{name}: {len(inbounds)} inbound(s)"
    return f"system 3x-ui: {len(inbounds)} inbound(s)"


def _inbound_id(inbound: dict[str, Any]) -> int:
    return int(inbound.get("id") or 0)


def _inbound_node_id(inbound: dict[str, Any]) -> int | None:
    value = inbound.get("nodeId")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _inbound_enabled(inbound: dict[str, Any]) -> bool:
    if _inbound_id(inbound) <= 0:
        return False
    if inbound.get("enable") is False:
        return False
    protocol = str(inbound.get("protocol") or "").lower()
    return protocol in {"", "vless", "vmess", "trojan", "shadowsocks", "hysteria", "hysteria2"}


def _node_id(node: dict[str, Any]) -> int | None:
    try:
        return int(node.get("id") or 0) or None
    except (TypeError, ValueError):
        return None


def _node_enabled(node: dict[str, Any]) -> bool:
    if not node:
        return True
    if node.get("enable") is False:
        return False
    return str(node.get("status") or "online").lower() != "offline"


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
