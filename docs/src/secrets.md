# Secrets: token, database and DCS passwords

DCSServerBot does not keep secrets in the YAML you edit. It moves them into separate files the first
time it starts and leaves a marker in the configuration:

```yaml
# config/main.yaml — the bot replaces the password with SECRET
database:
  url: postgresql://dcssb:SECRET@localhost:5432/dcssb
```

This applies to the Discord token, the database password, the DCS password, and a proxy password if
you configure one. The config directory can therefore be copied, backed up or shown to someone
without handing over the credentials.

## Where they go

Every secret is one pickled file in `config/.secret/`, named after its key:

| Secret | File |
|:-------|:-----|
| Discord token | `config/.secret/token.pkl` |
| Database password | `config/.secret/database.pkl` (cluster database: `clusterdb.pkl`) |
| Proxy password | `config/.secret/proxy.pkl` |
| DCS password | `config/.secret/DCS.pkl` |

The directory is created on first use and hidden on Windows.

## Replacing a value

Add the key back to the YAML and restart:

```yaml
# config/services/bot.yaml
token: YOUR_NEW_TOKEN
```

On the next start the bot stores the new value and strips the line again. The same works for the
database URL, the proxy password and the DCS password.

## Reading a value you forgot

Start the bot with `-s` (for example `run.cmd -s`) and it prints the stored secrets before it starts
up. Treat that output as sensitive: it is on screen and in the console scrollback.

!!! warning
    Never commit a configuration file with a real token in it. GitHub revokes a token it finds in a
    public repository automatically, and you then have to issue a new one.
