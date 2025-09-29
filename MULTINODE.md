# Multi-Node-Setup
Larger groups do not only run one PC with (multiple) DCS servers, but they run multiple (virtual) PCs, sometimes even 
over multiple locations. DCSServerBot supports many of these configurations.

## Prerequisites
To support a multi-node setup (or cloud setup), you should prepare your environment.<br>
Let's first clarify a few terms again:

- **Guild**<br>
This is the virtual bracket around everything. Your _Guild_ ID is your Discord server ID. One bot with an unlimited number 
of nodes will form a DCSServerBot cluster under this ID.

- **Node**<br>
A _Node_ is a single installation of DCSServerBot. You can run multiple nodes on one PC (see below), or you can run them
on different PCs. Each node is a single Python process. Only one of the nodes can be a master. The cluster will 
determine automatically which one that is and will automatically switch the master to another node on system downtimes 
or outages.

If you want to run your DCSServerBot over multiple locations, you need to prepare your setup:

## Cloud-Drive Setup (default)
You need to make sure that every node in your cluster is aware of the full configuration. The best way to achieve this
is to have the bot or at least the bot's configuration installed on a cloud drive, like OneDrive or Google Drive. 
I personally prefer OneDrive, as its technology is superior to Google Drive. But any cloud drive or NAS should work.

Add these lines to your configuration:

a) main.yaml:
```yaml
autoupdate: true
```

b) nodes.yaml
```yaml
Node1:  # this is the name of your first node (will be different for you, usually the hostname is used)
  cloud_drive: true # this is the default, so no need to specify it in here, just for reference    
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here if your nodes are not on the same network (default = 30)
  database:
    url: postgres://dcsserverbot:SECRET@127.0.0.1:5432/dcsserverbot?sslmode=prefer  # if your database is installed on Node1
# ... anything else like extensions, instances, ... for Node1
Node2:  # this is the name of your second node (will be different for you, usually the hostname is used)
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here if your nodes are not on the same network (default = 30)
  database:
    url: postgres://dcsserverbot:SECRET@xxx.xxx.xxx.xxx:5432/dcsserverbot?sslmode=prefer  # replace xxx.xxx.xxx.xxx with the IP of Node1
# ... anything else like extensions, instances, ... for Node2
Node3:  # this is the name of your third node (will be different for you, usually the hostname is used)
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here if your nodes are not on the same network (default = 30)
  database:
    url: postgres://dcsserverbot:SECRET@xxx.xxx.xxx.xxx:5432/dcsserverbot?sslmode=prefer  # replace xxx.xxx.xxx.xxx with the IP of Node1
# ... anything else like extensions, instances, ... for Node3
```

## Config-Sync Setup
If you decide to only sync the configuration, your bots will behave differently on auto-update.

a) main.yaml:
```yaml
autoupdate: true
```

b) nodes.yaml
```yaml
Node1:  # this is the name of your first node (will be different for you, usually the hostname is used)
  cloud_drive: false  # tell the bot that it is NOT installed on a cloud drive    
# ... same as above
Node2:  # this is the name of your second node (will be different for you, usually the hostname is used)
  cloud_drive: false  # tell the bot that it is NOT installed on a cloud drive    
# ... same as above
Node3:  # this is the name of your third node (will be different for you, usually the hostname is used)
  cloud_drive: false  # tell the bot that it is NOT installed on a cloud drive    
# ... same as above
```

## Master Handling
The DCSServerBot cluster consists of one master node and multiple agent nodes. 
The master holds some services that are only allowed to run once in your cluster, like the Discord bot.
As this is usually linked to heavy database access, it is recommended to have the master node on the same server that
holds the database.
Installations which use a central database on a dedicated server or even a cloud database do not need to consider this.

If you want to bind your master to a specific node, you set this node as `preferred_master: true`. 
In this case and unless this node does not run, it will be the master node.

In rare cases it might be that you do not want a node to become master at all.
This might be true if you run a node on a remotely hosted server like with Fox3 (or any other hoster), you can't 
sync the configuration, or any other reason that might prevent you from having this node as master.
In this case you can configure `no_master: true` for this node.

```yaml
Node1:  # this is the name of your first node (will be different for you, usually the hostname is used)
  preferred_master: true  # Optional: if your database is installed on Node1, make it your preferred master node
# ... same as above
Node2:  # this is the name of your second node (will be different for you, usually the hostname is used)
# ... same as above
Node3:  # this is the name of your third node (will be different for you, usually the hostname is used)
  no_master: true   # Optional: This node will never become a master
# ... same as above
```
> [!IMPORTANT]
> If no node is available to take over the master, your cluster will not work.

## PostgreSQL Setup
A standard PostgreSQL installation does not allow remote access to the database. To change it, follow [this guide](https://blog.devart.com/configure-postgresql-to-allow-remote-connection.html).
In addition, you would need to allow external access to your database by forwarding the database port (default: 5432)
from your router to the PC running the database. You can also get a cloud database. There are many providers out there
where you can rent one for relatively small money.
> [!IMPORTANT]
> For a secure communication, you should consider enabling SSL in your database. A howto for that would be too much
> for this little guide, but there are lots of guides available on the web how to do that.

## Running multiple versions of DCS World on one PC
One DCSServerBot node can run as many DCS servers as your PC can handle, but they all share the very same DCS World 
installation. This means you can **not** run two different DCS World installations with separate root mods installed.<br>
To achieve this, you need to run two or more nodes on one PC.<br>
DCSServerBot uses the hostname of the PC as a node name, if not specified otherwise. To be able to run multiple nodes on
the same PC, you need to specify an additional parameter -n (or --node) on startup (e.g. `run -n node01`).<br>
This will start a new node (you'll be prompted for the installation of it, if it does not exist yet), with the name
"node01". 
> [!IMPORTANT]
> The node-name has to be unique in your **whole cluster**.

## Running multiple Nodes on multiple PCs
If you set up your environment with a cloud drive for your installation and a central database that is accessible from
every node, the installation of an additional node is quite straight forward.<br>
You need to run `run.cmd` or `install.cmd` on your new node and the installer will guide you through your 
installation. If not specified otherwise, the node-name will be the hostname of the respective node.

> [!TIP]
> If you use multiple nodes on multiple PCs, you might get to the moment where instances are named identically. 
> This will start with the first instance already if you keep the default name "DCS.release_server".<br>
> As many configuration files only use the instance name per default, you might need to add the node name as well.
> This can be done the same as it is already in your nodes.yaml: The node can be the outer structure in each config file.
> 
> Single-Node-Config:
> ```yaml
> DEFAULT:
>   some-param: A
> DCS.release_server:
>   some-param: B
> ```
> 
> Multi-Node-Config:
> ```yaml
> DEFAULT:
>   some-param: A
> MyNode1:
>   DCS.release_server:
>     some-param: B
> MyNode2:
>   DCS.release_server:
>     some-param: C
> ```
DCSServerBot will understand both versions. The DEFAULT will be used for ALL instances, independent of which node they 
are on. If you don't provide a node in a multi-node-system, the bot will read the same parameters for all instances 
that are named DCS.release_server on any of your nodes. This can be what you want, but it can lead to errors.<br>
I would always recommend creating the node-specific version (ex: "Multi-Node-Config" above) to avoid confusion. That's 
what the bot will create during a default installation also.

### Moving a Server from one Node / Instance to another
Each server is loosely coupled to an instance on a node. You can migrate a server to another instance though, by using
the `/server migrate` command. 
> [!NOTE]
> Unless you use a central missions directory, the necessary missions (or scripts) for this server might not be 
> available on the other node and the migration will end up in an incomplete state.
