import requests
import networkx as nx
import matplotlib.pyplot as plt
import json
import os
import threading
import queue
import time
import sys
import select

RYU_API = "http://127.0.0.1:8080"
TOPOLOGY_API = f"{RYU_API}/v1.0/topology"
SNAPSHOT_FILE = os.path.join(os.path.dirname(__file__), "snapshot.json")

stop_real_time = threading.Event()
fetch_queue = queue.Queue()
initial_host_mapping = None
initial_switch_links = None


def fetch_topology():
    """Fetching switches and links from RYU's REST API."""
    try:
        switches = requests.get(f"{TOPOLOGY_API}/switches").json()
        links = requests.get(f"{TOPOLOGY_API}/links").json()
        return switches, links
    except Exception as e:
        print(f"Error fetching topology: {e}")
        return [], []


def fetch_hosts():
    """Fetching hosts from RYU's REST API and format them properly."""
    try:
        response = requests.get(f"{TOPOLOGY_API}/hosts")
        response.raise_for_status()
        hosts_data = response.json()
        hosts = []
        for mac, info in hosts_data.items():
            hosts.append({
                "mac": mac,
                "attached_switch": info["attached_switch"],
                "attached_port": info["attached_port"],
            })

        return hosts
    except Exception as e:
        print(f"Error fetching hosts: {e}")
        return []


def save_snapshot(switches, links, host_links):
    """Saving topology and traffic data to a JSON file."""
    snapshot = {
        "switches": switches,
        "links": links,
        "host_links": host_links,
    }
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(f"Snapshot saved to {SNAPSHOT_FILE}")


def load_snapshot():
    """Loading topology and traffic data from a JSON file."""
    try:
        with open(SNAPSHOT_FILE, "r") as f:
            snapshot = json.load(f)
            return snapshot["switches"], snapshot["links"], snapshot["host_links"]
    except Exception as e:
        print(f"Error loading snapshot: {e}")
        return [], [], []


def deduplicate_links(links):
    """Deduplicating bidirectional links to consider them as one."""
    unique_links = set()
    for link in links:
        src = (link["src"]["dpid"], link["src"]["port_no"])
        dst = (link["dst"]["dpid"], link["dst"]["port_no"])
        unique_links.add(tuple(sorted((src, dst))))
    return list(unique_links)


def filter_host_links(links, hosts):
    """Filtering host-to-switch links."""
    switch_ports = {}
    for link in links:
        src_dpid = link["src"]["dpid"]
        src_port = link["src"]["port_no"]
        dst_dpid = link["dst"]["dpid"]
        dst_port = link["dst"]["port_no"]

        if src_dpid not in switch_ports:
            switch_ports[src_dpid] = set()
        if dst_dpid not in switch_ports:
            switch_ports[dst_dpid] = set()

        switch_ports[src_dpid].add(src_port)
        switch_ports[dst_dpid].add(dst_port)

    host_links = []
    host_counter = 1
    for host in sorted(hosts, key=lambda h: (int(h["attached_switch"]), int(h["attached_port"]))):
        switch_id = int(host["attached_switch"])
        port_no = int(host["attached_port"])

        if switch_id not in switch_ports or port_no not in switch_ports[switch_id]:
            host_name = f"h{host_counter}"
            host_counter += 1
            host_links.append({
                "host_name": host_name,
                "host_mac": host["mac"],
                "switch_dpid": switch_id,
                "port_no": port_no
            })

    return host_links


def remove_link(G, source, destination, switches, links, host_links, all_hosts):
    """Removing a link from the graph."""
    try:
        if source.startswith("h"):  # Source is a host
            src = source
        else:  # Source is a switch
            src = int(source)

        if destination.startswith("h"):  # Destination is a host
            dst = destination
        else:  # Destination is a switch
            dst = int(destination)

        # Checking and removing link
        if G.has_edge(src, dst):
            G.remove_edge(src, dst)
            print(f"Link removed: {src} <-> {dst}")

            # Updating topology data
            if isinstance(src, int) and isinstance(dst, int):  # Switch-to-switch link
                links[:] = [
                    link for link in links if not ({link[0][0], link[1][0]} == {src, dst})
                ]
            elif isinstance(src, str) or isinstance(dst, str):  # Host-to-switch link
                host_links[:] = [
                    link for link in host_links
                    if not ((link["host_name"] == src and link["switch_dpid"] == dst) or
                            (link["host_name"] == dst and link["switch_dpid"] == src))
                ]

                # Ensuring the host remains in the graph
                if isinstance(src, str) and src in all_hosts:
                    G.add_node(src)
                if isinstance(dst, str) and dst in all_hosts:
                    G.add_node(dst)

            # Redrawing updated graph
            visualize_topology(G, switches, links, host_links)
        else:
            print("Link does not exist in the current topology.")
    except ValueError:
        print("Invalid input. Switch IDs must be integers, and host IDs must start with 'h'.")


def add_link(G, source, destination, switches, links, host_links, host_links_snapshot):
    """Adding a link to the graph."""
    global initial_switch_links
    try:
        if source.startswith("h"):  # Source is a host
            src = source
        else:  # Source is a switch
            src = int(source)

        if destination.startswith("h"):  # Destination is a host
            dst = destination
        else:  # Destination is a switch
            dst = int(destination)

        # Checking if the link exists in the original topology or snapshot
        is_valid_link = False
        port_no = None
        if isinstance(src, int) and isinstance(dst, int):  # Switch-to-switch link
            for link in initial_switch_links:
                if {link[0][0], link[1][0]} == {src, dst}:
                    is_valid_link = True
                    port_no = (link[0][1] if link[0][0] == src else link[1][1])
                    break
        elif isinstance(src, str) or isinstance(dst, str):  # Host-to-switch link
            for host_link in host_links_snapshot:
                if (host_link["host_name"] == src and host_link["switch_dpid"] == dst) or \
                   (host_link["host_name"] == dst and host_link["switch_dpid"] == src):
                    is_valid_link = True
                    port_no = host_link["port_no"]
                    break

        if is_valid_link:
            # Adding the link to the graph
            G.add_edge(src, dst)
            print(f"Link added: {src} <-> {dst}")

            # Updating topology data
            if isinstance(src, int) and isinstance(dst, int):  # Switch-to-switch link
                links.append(((src, port_no), (dst, None)))
            elif isinstance(src, str) or isinstance(dst, str):  # Host-to-switch link
                host_links.append({
                    "host_name": src if isinstance(src, str) else dst,
                    "switch_dpid": dst if isinstance(dst, int) else src,
                    "port_no": port_no
                })

            # Redrawing the updated graph
            visualize_topology(G, switches, links, host_links)
        else:
            src_type = "switch" if isinstance(src, int) else "host"
            dst_type = "switch" if isinstance(dst, int) else "host"
            print(f"src and dst not connected in original topology: {src_type} {src} and {dst_type} {dst}")
    except ValueError:
        print("Invalid input. Switch IDs must be integers, and host IDs must start with 'h'.")


def pingall(G, all_hosts):
    """Simulating the pingall functionality."""
    # Include all hosts
    hosts = sorted(all_hosts)
    total_pings = len(hosts) * (len(hosts) - 1)  # All pairwise pings
    successful_pings = 0

    print("\n=== Pingall Simulation ===")
    for src in hosts:
        reachable = []
        for dst in hosts:
            if src != dst:
                # Checking if the source and destination exist in the graph
                if G.has_node(src) and G.has_node(dst) and nx.has_path(G, src, dst):
                    reachable.append(dst)
                    successful_pings += 1
        print(f"{src} -> {' '.join(reachable) if reachable else 'Unreachable'}")

    # Calculating packet loss
    packet_loss_percentage = 100 - ((successful_pings / total_pings) * 100) if total_pings > 0 else 0
    print(f"*** Results: {packet_loss_percentage:.2f}% packet loss ({successful_pings}/{total_pings} received)")


def visualize_topology(G, switches, links, host_links):
    """Visualizing the network topology with hosts included."""
    G.clear()
    G.add_nodes_from(switches)

    # Adding switch-to-switch links
    graph_links = [(link[0][0], link[1][0]) for link in links]
    G.add_edges_from(graph_links)

    # Adding hosts and host-to-switch links
    for host_link in host_links:
        host_node = host_link["host_name"]
        switch_node = host_link["switch_dpid"]
        G.add_node(host_node)
        G.add_edge(switch_node, host_node)

    # Ensuring isolated hosts are also in the graph
    for node in G.nodes:
        if isinstance(node, str) and node.startswith("h") and not list(G.neighbors(node)):
            G.add_node(node)

    # Clearing the existing plot
    plt.clf()

    pos = nx.spring_layout(G, seed=42)

    nx.draw_networkx_nodes(
        G, pos, nodelist=switches, node_size=700, node_color="lightblue", label="Switches"
    )
    nx.draw_networkx_labels(
        G, pos, labels={node: f"S{node}" for node in switches}, font_weight="bold"
    )
    nx.draw_networkx_edges(G, pos, edgelist=graph_links, width=1, edge_color="black", label="Links")

    host_nodes = [node for node in G.nodes if isinstance(node, str) and node.startswith("h")]
    nx.draw_networkx_nodes(
        G, pos, nodelist=host_nodes, node_size=300, node_color="green", label="Hosts"
    )
    nx.draw_networkx_labels(
        G, pos, labels={node: node for node in host_nodes}, font_size=8
    )
    host_links_edges = [(link["switch_dpid"], link["host_name"]) for link in host_links]
    nx.draw_networkx_edges(G, pos, edgelist=host_links_edges, edge_color="gray", style="dashed")

    plt.title("Digital Twin - Network Topology")
    plt.axis("off")
    plt.legend()
    plt.pause(0.001)


def initialize_host_mapping(links, hosts):
    """
    Initializing the host-to-switch mapping.
    """
    # Deduplicate switch-to-switch links
    switch_ports = {}
    for link in links:
        src_dpid = link["src"]["dpid"]
        src_port = link["src"]["port_no"]
        dst_dpid = link["dst"]["dpid"]
        dst_port = link["dst"]["port_no"]

        if src_dpid not in switch_ports:
            switch_ports[src_dpid] = set()
        if dst_dpid not in switch_ports:
            switch_ports[dst_dpid] = set()

        switch_ports[src_dpid].add(src_port)
        switch_ports[dst_dpid].add(dst_port)

    # Mapping hosts to switches
    host_links = []
    host_counter = 1
    for host in sorted(hosts, key=lambda h: (int(h["attached_switch"]), int(h["attached_port"]))):
        switch_id = int(host["attached_switch"])
        port_no = int(host["attached_port"])

        # Excluding ports used for switch-to-switch links
        if switch_id in switch_ports and port_no in switch_ports[switch_id]:
            continue

        # Adding valid host-to-switch link
        host_name = f"h{host_counter}"
        host_counter += 1
        host_links.append({
            "host_name": host_name,
            "host_mac": host["mac"],
            "switch_dpid": switch_id,
            "port_no": port_no,
        })

    return host_links


def real_time_update():
    """
    Continuously fetching real-time updates, updating the switch-to-switch links, and re-drawing the graph.
    """
    global initial_host_mapping, initial_switch_links
    print("\nReal-time fetching and synchronization active...")
    switches, links = fetch_topology()
    hosts = fetch_hosts()

    # Initializing the host and switch mapping if not already set
    if initial_host_mapping is None:
        initial_host_mapping = initialize_host_mapping(links, hosts)
    if initial_switch_links is None:
        initial_switch_links = deduplicate_links(links)

    while not stop_real_time.is_set():
        # Fetching updated switch-to-switch links
        switches, links = fetch_topology()
        deduplicated_links = deduplicate_links(links)

        # Using the initial host mapping
        current_host_links = initial_host_mapping

        # Updating the graph visualization
        fetch_queue.put((switches, deduplicated_links, current_host_links))
        time.sleep(1)  # Fetch updates every 1 second


def is_enter_pressed():
    """Checking if the Enter key is pressed."""
    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.read(1)
        if line.strip() == "":
            return True
    return False


if __name__ == "__main__":
    plt.ion()
    G = nx.Graph()

    while True:
        mode = input("\nChoose mode: real-time (real), snapshot (snapshot), or exit (exit): ").strip().lower()

        if mode == "exit":
            print("Exiting the program.")
            break

        if mode == "real":
            stop_real_time.clear()
            real_time_thread = threading.Thread(target=real_time_update, daemon=True)
            real_time_thread.start()

            print("Press Enter to stop real-time fetching and switch to snapshot mode...")
            while not stop_real_time.is_set():
                if is_enter_pressed():
                    print("\nReal-time fetching stopped. You can now interact with the snapshot.")
                    stop_real_time.set()
                try:
                    switches, links, host_links = fetch_queue.get(timeout=1)
                    visualize_topology(G, switches, links, host_links)
                except queue.Empty:
                    pass

        elif mode == "snapshot":
            print("Capturing snapshot...")
            switches, links = fetch_topology()
            hosts = fetch_hosts()

            deduplicated_links = deduplicate_links(links)

            # Using initial_host_mapping if available; otherwise, generate it
            if initial_host_mapping is None:
                initial_host_mapping = initialize_host_mapping(deduplicated_links, hosts)

            host_links = initial_host_mapping  # Always using the saved mapping

            save_snapshot(switches, deduplicated_links, host_links)
            print("Snapshot completed.")

            replay_now = input("Do you want to replay the snapshot now? (yes/no): ").strip().lower()
            if replay_now == "yes":
                mode = "no"

            if mode == "no":
                switches, links, host_links = load_snapshot()

                if not switches:
                    print("No valid snapshot found. Please take a snapshot first.")
                    continue

                print("\n=== Snapshot Details ===")
                print(f"Switches ({len(switches)}): {switches}")
                print(f"Switch-to-Switch Links ({len(links)}):")
                for link in links:
                    print(f"  Switch {link[0][0]} <-> Switch {link[1][0]}")
                print(f"Host-to-Switch Links ({len(host_links)}):")
                for host_link in host_links:
                    print(f"  {host_link['host_name']} connected to Switch {host_link['switch_dpid']} Port {host_link['port_no']}")

                # Initializing all_hosts list
                all_hosts = sorted({link["host_name"] for link in host_links})
                for node in switches:
                    if isinstance(node, str) and node.startswith("h") and node not in all_hosts:
                        all_hosts.append(node)

                # Saving original snapshots for reference
                links_snapshot = list(links)
                host_links_snapshot = list(host_links)

                visualize_topology(G, switches, links, host_links)

                while True:
                    action = input("\nChoose action: link (link), pingall (pingall), or exit replay (exit): ").strip().lower()

                    if action == "link":
                        link_action = input("Do you want to bring a link up or down? (up/down): ").strip().lower()
                        source = input("Enter source node ID (e.g., '1' for Switch 1 or 'h1' for Host 1): ").strip()
                        destination = input("Enter destination node ID (e.g., '2' for Switch 2 or 'h2' for Host 2): ").strip()

                        if link_action == "down":
                            remove_link(G, source, destination, switches, links, host_links, all_hosts)
                        elif link_action == "up":
                            add_link(G, source, destination, switches, links, host_links, host_links_snapshot)
                        else:
                            print("Invalid option. Please choose 'up' or 'down'.")
                    elif action == "pingall":
                        pingall(G, all_hosts)
                    elif action == "exit":
                        break

