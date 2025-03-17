# Digital-Twin-for-SDN-Networks

HOW TO START THE PROJECT:

1. As I am running my vagrant setup on the VMware Workstation 17 player, I had to download the helper software called "XLaunch" to make my Windows machine be able to communicate with it and understand on which port the vagrant is running. This is not required on each Windows machine but there might be need to also make it for your computer. Therefore download "XLaunch" software from: https://sourceforge.net/projects/xming/

Then run it and select --> "Multiple windows" (Next) --> "Start no client" (Next) --> No extra settings (Next) --> (Finish) 



2. Login to comnetsemu by running the VM named "next_generation_of_networks.vmx" which is located under the folder named "networking_vagrant":

comnetsemu login: vagrant, Password: vagrant



3. Then open a new Windows PowerShell terminal and start connection with "ssh -X vagrant@192.168.255.129" (This is for the  mininet but do not start it yet)



4. Then open a new Windows PowerShell terminal (--> ssh -X vagrant@192.168.255.129) for the controller and navigate to the correct folder path (~/comnetsemu_dependencies/ryu-v4.34/ryu/ryu/app) and then run the command: "ryu-manager --observe-links rest_topology_with_switch.py"


5. Then go to the first Windows PowerShell terminal and start the mininet network, e.g., "sudo mn --controller=remote,ip=127.0.0.1,port=6653 --topo=tree,depth=3" --> then you can test the ping functionality between hosts with "pingall" and observe whether the controller correctly works.



6. Then open a new Windows PowerShell terminal (--> ssh -X vagrant@192.168.255.129) and navigate to the correct folder path (~/comnetsemu_dependencies/ryu-v4.34/ryu/ryu/app) and then run the digital twin script: "python3 saved_network_visualize_topology.py" --> after waiting a while, you should be able to see a menu with two options: real-time and snapshot --> you can start the real time option and see the network topology in real-time.



Note: There are also a folder called "scripts" where all the written scripts done by me can be found.
