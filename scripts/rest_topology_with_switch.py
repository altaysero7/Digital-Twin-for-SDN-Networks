from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.app.wsgi import WSGIApplication, ControllerBase, Response, route
from ryu.topology.api import get_switch, get_link
import json

class RestTopology(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        'wsgi': WSGIApplication,
    }

    def __init__(self, *args, **kwargs):
        super(RestTopology, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # Track MAC-to-port mappings
        self.hosts = {}  # Track hosts and their connected switches/ports
        wsgi = kwargs['wsgi']
        wsgi.register(RestTopologyController, {'topology_app': self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Install a default table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP packets (used for link discovery)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)

        # Learn MAC address to avoid flooding next time
        self.mac_to_port[dpid][src] = in_port

        # Register the host and its connection
        if src not in self.hosts:
            self.hosts[src] = {"attached_switch": str(dpid), "attached_port": str(in_port)}
            self.logger.info("Host %s connected to switch %s, port %s", src, dpid, in_port)

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow rule to avoid Packet-In next time
        match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
        self.add_flow(datapath, 1, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)


class RestTopologyController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RestTopologyController, self).__init__(req, link, data, **config)
        self.topology_app = data['topology_app']

    @route('topology', '/v1.0/topology/switches', methods=['GET'])
    def list_switches(self, req, **kwargs):
        switches = get_switch(self.topology_app)
        body = json.dumps([switch.dp.id for switch in switches])
        return Response(content_type='application/json', body=body)

    @route('topology', '/v1.0/topology/links', methods=['GET'])
    def list_links(self, req, **kwargs):
        links = get_link(self.topology_app)
        link_list = []
        for link in links:
            link_list.append({
                "src": {"dpid": link.src.dpid, "port_no": link.src.port_no},
                "dst": {"dpid": link.dst.dpid, "port_no": link.dst.port_no}
            })
        body = json.dumps(link_list)
        return Response(content_type='application/json', body=body)

    @route('topology', '/v1.0/topology/hosts', methods=['GET'])
    def list_hosts(self, req, **kwargs):
        """Return dynamically discovered host information."""
        hosts = self.topology_app.hosts
        body = json.dumps(hosts)
        return Response(content_type='application/json', body=body)
