# Architecture

## Overview
Each DCSServerBot installation consists out of several components:

### Node
Each `Node` specifies a bot installation, typically on one PC. If you run more than one PC hosting DCS servers
maybe even over multiple locations worldwide, you have a Node for each of your PCs in that virtual `Cluster`.<br>
One of the Nodes is the `Master`. This one hosts the [Discord bot](./services/bot/README.md), that connects to 
Discord and provides all the commands that you can use. It will always only run on __one node__ in your cluster.

> ❗ If the master node fails, any other node in your cluster automatically takes over the task of being the bot.

### Instance
Each `Instance` specifies a DCS executable that can be launched on that specific node. The name of the instance matches
your Saved Games directory. The single instance that every server should have is DCS.server or DCS.openbeta_server.

Each instance has its own UDP communication port (default 6666). It will contact the DCSServerBot via UDP usually 
over port 10042. The whole communication is UDP based. This has a slight risk of data loss, but is non-blocking
and much faster. Very large servers run DCSServerBot. It has a small payload in DCS and only has a very small 
performance impact on your servers.

### Server
Each `Server` specifies a __configuration__ that can be loaded into an instance. Even if there is usually a 1-to-1 
connection between instances and servers, servers might be moved from one instance to another. Imagine your instance 
providing power, availability and maybe components like TacView, LotAtc, SRS and your server configuration having a 
name, the number of players and a password. You can easily move around that information from one Instance to another, 
even to another Node. That's why I decided to loosely couple that information and not hard code it into each node.

> ❗ A standard configuration will only have one node and probably only one server.

## Objects inside your Bot
Each bot node is an independently running Python process, unless you have a multi-node configuration, this will be your 
single bot that you run.

```
Node
|_ Services
   |_ Bot
   |_ ServiceBus
   |_ DashBoard
   |_ Monitoring
   |_ Backup (optional)
   |_ OvGME (optional)
   |_ Music (optional)
|_ Extensions (all optional)
   |_ DCS-SRS
   |_ Tacview
   |_ LotAtc
   |_ ...
|_ Plugins
   |_ Admin
   |_ Mission
   |_ CreditSystem
   |_ Help
   |_ ...
   |_ Plugin n (optional)
```
### Service
A `Service` is a singleton that runs either once on your node or even once in a cluster (like the Bot service).
It has access to all your local drives and processes. If you need access to that, you need to write a service.
You can find more information on how to do that [here](./services/README.md).

### Plugins
`Plugins` are modules that only run on your bot. This is important to know and to understand, because they for 
instance don't have easy access to some file system on any remote node. They communicate with any other object like
other nodes, services on other nodes or other servers via so called `Proxies`. A proxy provides the same interface as
the real object. If you for instance access any server object, you access the real implementation if that server is
running on the same node or 

DCSServerBot comes with a lot of services and plugins already, that are all described in more detail in this documentation.<br>
You can enhance DCSServerBot by implementing your own services and / or plugins (or hybrids). You'll find a howto 
[here](./plugins/README.md) and [here](./services/README.md). 



## Multi-Node Configurations

#### Overview: DCSServerBot Cluster
```
DCSServerBot Cluster
|_ Node 1 (= Host 1)
   |_ Instance 1 <=> DCS Server 11
   |_ ..
   |_ Instance n <=> DCS Server 1n
|_ ...
|_ Node m (= Host m)
   |_ Instance 1 <=> DCS Server m1
   |_ ..
   |_ Instance n <=> DCS Server mn
```

If you plan to run multiple bots over multiple PC or even locations, you should do some preparation for it, to make
it easy for you and the bot.

### Cloud Drives
Some files are more easily shared across cloud drives. This includes missions and music, but even the bot itself can be
installed on a cloud drive already. This helps you to keep a single configuration for all your bot nodes.

> ⚠️ **Attention!**
> At the moment, the Python virtual environment (venv) is stored on that cloud drive also, causing some delays in sync.
> You might need to wait for the drive content to be synced on all nodes before starting the 2nd bot node.

### Cloud Database
You can either host a database on one of your nodes, which creates another single point of failure, or have a cloud
database at some of the prominent cloud services. As the bot databases don't get that large, it should not be a big 
deal. I have one for $7 a month, running our 2 bots and 10 servers on it. That's an easy investment.

### Automatic Failover
At the moment, the bot can do automatic failovers for the Discord bot service. This will make sure that the bot is
responsive on Discord at any time, even if one of your nodes crashes (prerequisite: cloud database).

_It is planned, to have automatic server takeovers also. That means, if you have a very prominent server that it can
for instance take down your development instance on another host, if needs be, and make sure the production server
is up and running 24x7. As DCS does not support hot migration, the mission will start fresh like after a restart._
