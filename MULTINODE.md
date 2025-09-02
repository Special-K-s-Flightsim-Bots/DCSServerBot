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
A _Node_ is a single installation of DCSServerBot. You can run multiple nodes on one PC (see below) or you can run them
on different PCs. Each node is a single Python process. Only one of the nodes can be a master. The cluster will 
determine automatically, which one that is and will automatically switch the master to another node on system downtimes 
or outages.

If you want to run your DCSServerBot over multiple locations, you need to prepare your setup:

## Cloud-Drive Setup (default)
You need to make sure, that every node in your cluster is aware of the full configuration. The best way to achieve this
is to have the bot or at least the bots configuration installed on a cloud drive, like OneDrive or Google Drive. 
I personally prefer OneDrive, as its technology is superior to Google Drive. But any cloud drive or NAS should work.

Add these lines to your configuration:

a) main.yaml:
```yaml
autoupdate: true
```

b) nodes.yaml
```yaml
Node1:  # this is the name of your first node. will be different for you
  cloud_drive: true # this is the default, so no need to specify it in here, just for reference    
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here (default = 30)
  preferred_master: true  # if your database is installed on Node1, make it your preferred master node
  database:
    url: postgres://dcsserverbot:SECRET@127.0.0.1:5432/dcsserverbot?sslmode=prefer  # if your database is installed on Node1
# ... anything else like extensions, instances, ... for Node1
Node2:  # this is the name of your second node. will be different for you
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here (default = 30)
  database:
    url: postgres://dcsserverbot:SECRET@xxx.xxx.xxx.xxx:5432/dcsserverbot?sslmode=prefer  # replace xxx.xxx.xxx.xxx with the IP of Node1
# ... anything else like extensions, instances, ... for Node2
Node3:  # this is the name of your third node. will be different for you
  heartbeat: 60     # sometimes a larger heartbeat makes the connection between the nodes more stable. I recommend using 60 here (default = 30)
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
Node1:  # this is the name of your first node. will be different for you
  cloud_drive: false  # tell the bot that you are NOT installed on a cloud drive    
# ... same as before
Node2:  # this is the name of your second node. will be different for you
  cloud_drive: false  # tell the bot that you are NOT installed on a cloud drive    
# ... same as before
Node3:  # this is the name of your third node. will be different for you
  cloud_drive: false  # tell the bot that you are NOT installed on a cloud drive    
# ... same as before
```

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
installation. This means, you can **not** run two different DCS World installations with separate root mods installed.<br>
To achieve this, you need to run two or more nodes on one PC.<br>
DCSServerBot uses the hostname of the PC as node name, if not specified otherwise. To be able to run multiple nodes on
the same PC, you need to specify an additional parameter -n (or --node) on startup (e.g. `run -n node01`).<br>
This will start a new node (you'll be prompted for the installation of it, if it does not exist yet), with the name
"node01". 
> [!IMPORTANT]
> The node-name has to be unique in your **whole cluster**.

## Running multiple Nodes on multiple PCs
If you set up your environment with a cloud drive for your installation and a central database that is accessible from
every node, the installation of an additional node is quite straight forward.<br>
You just need to run `run.cmd` or `install.cmd` on your new node and the installer will guide you through your 
installation. If not specified otherwise, the node-name will be the hostname of the respective node.

> [!TIP]
> If you use multiple nodes on multiple PCs, you might get to the moment where instances are named identical. 
> This will start with the first instance already, if you keep the default name "DCS.release_server".<br>
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
DCSServerBot will understand both versions. The DEFAULT will be used for ALL instances, independent on which node they 
are on. If you don't provide a node in a multi-node-system, the bot will read the same parameters for all instances 
that are named DCS.release_server on any of your nodes. This can be what you want, but it can lead to errors.<br>
I would always recommend to create the node-specific version (ex: "Multi-Node-Config" above) to avoid confusion. That's 
what the bot will create during a default installation also.

### Moving a Server from one Node / Instance to another
Each server is loosely coupled to an instance on a node. You can migrate a server to another instance though, by using
the `/server migrate` command. 
> [!NOTE]
> Unless you use a central missions directory, the necessary missions (or scripts) for this server might not be 
> available on the other node and the migration will end up in an incomplete state.
