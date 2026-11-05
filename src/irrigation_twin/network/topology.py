"""Network structure with NetworkX: which pipes each plot depends on.

The hydraulic solver says how much water arrives. The graph says by which
routes it can arrive. For each plot this module lists every supply route
from the water sources (the sump/pump and the tank) and the *critical*
pipes, those whose failure alone cuts the plot off from every source. This
is the node-to-plot mapping in the project's tech stack, and it explains
the scenario results: a break on a critical pipe starves its plot, while a
plot with two routes keeps some supply through the loop.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd
import wntr

from .build import PLOT_JUNCTIONS, RESERVOIR, TANK

SOURCES = (RESERVOIR["name"], TANK["name"])
HUB = "J2"


def network_graph(wn: wntr.network.WaterNetworkModel) -> nx.Graph:
    """Undirected graph of the network: nodes are junctions/sources, edges are pipes and the pump."""
    g = nx.Graph()
    for name, node in wn.nodes():
        g.add_node(name, kind=node.node_type, pos=node.coordinates)
    for name, link in wn.links():
        length = getattr(link, "length", 0.0)
        g.add_edge(link.start_node_name, link.end_node_name, link=name, kind=link.link_type, length=length)
    return g


def supply_routes(g: nx.Graph, junction: str, sources=SOURCES) -> list[list[str]]:
    """Every loop-free route from any source to ``junction``, as lists of link names."""
    routes = []
    for src in sources:
        for path in nx.all_simple_paths(g, src, junction):
            routes.append([g.edges[u, v]["link"] for u, v in zip(path, path[1:])])
    return routes


def critical_links(g: nx.Graph, junction: str, sources=SOURCES) -> list[str]:
    """Links whose loss alone disconnects ``junction`` from every source."""
    out = []
    for u, v, data in g.edges(data=True):
        h = g.copy()
        h.remove_edge(u, v)
        if not any(nx.has_path(h, s, junction) for s in sources):
            out.append(data["link"])
    return sorted(out)


def plot_supply_table(wn: wntr.network.WaterNetworkModel) -> pd.DataFrame:
    """One row per plot: its junction, pipe routes from the hub J2, critical pipes, shortest route length."""
    g = network_graph(wn)
    pipes_only = g.copy()
    pipes_only.remove_edges_from([(u, v) for u, v, d in g.edges(data=True) if d["kind"] != "Pipe"])
    rows = []
    for plot, j in PLOT_JUNCTIONS.items():
        # J2 is the hub where the pump main and the tank meet; routes are counted from there
        routes = list(nx.all_simple_paths(pipes_only, HUB, j))
        rows.append({"plot": plot, "junction": j, "routes_from_hub": len(routes),
                     "critical_pipes": ", ".join(critical_links(g, j)) or "none",
                     "shortest_route_m": round(nx.shortest_path_length(pipes_only, HUB, j, weight="length"), 1)})
    return pd.DataFrame(rows).set_index("plot")
