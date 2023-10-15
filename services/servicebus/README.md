# ServiceBus Service
This is the communication-hub of DCSServerBot.<br>
It allows you to interact between your DCS servers and DCSServerBot and between all nodes of a possible bot cluster.
The communication is either done in UDP or by using specific tables in the PostgreSQL database.

You usually don't need to touch this at all.

## Configuration
There is no dedicated configuration of the servicebus itself. Nevertheless, there are some settings that affect the
communication. All of them are in your nodes.yaml file.

```yaml
MyNode:
  listen_address: 0.0.0.0   # The interface the bot is listening to. Default: all interfaces (0.0.0.0)
  listen_port: 10042        # The bots listen port (default: 10042, same as FunkMan)
  slow_system: false        # If true, some communication timeouts will be increased (default: false)
  preferred_master: true    # Whenever this node is online, it will be the master (default: false)
  instances:
    DCS.openbeta_server:
      bot_port: 6666        # The port the DCS server listens on (default: 6666, increasing by one for each server)
```

## Tables
### NODES
All nodes are registered in this table. When a node does not update its information for more than 10s, it is considered
dead. If it was the master node, another node will take over the master role.

| Column    | Type                    | Description                              |
|-----------|-------------------------|------------------------------------------|
| #guild_id | BIGINT NOT NULL         | Discord Guild ID of this installation.   |
| #node     | TEXT NOT NULL           | Node name                                |
| master    | BOOLEAN NOT NULL        | Node is master?                          |
| last_seen | TIMESTAMP DEFAULT NOW() | Last update received (used as heartbeat) |


### INTERCOM
Intercom channel between all nodes.

| Column    | Type                    | Description                                 |
|-----------|-------------------------|---------------------------------------------|
| #id       | SERIAL                  | Auto-incrementing unique ID of this column. |
| node      | TEXT NOT NULL           | Receiving node for this message.            |
| data      | JSON                    | Payload of the message (JSON)               |
| time      | TIMESTAMP DEFAULT NOW() | Time of the message                         |

### FILES
Used for file interchange between the nodes.

| Column  | Type                    | Description                                 |
|---------|-------------------------|---------------------------------------------|
| #id     | SERIAL                  | Auto-incrementing unique ID of this column. |
| name    | TEXT NOT NULL           | Receiving node for this message.            |
| data    | BYTEA NOT NULL          | Binary file representation                  |
| created | TIMESTAMP DEFAULT NOW() | Time the file was uploaded                  |
