# Web Frontend – Linux

> Running the bot on Windows? Use the [Windows guide](webservice-frontend-windows.md) instead.

DCSServerBot comes with a web frontend that lets you manage your nodes and servers from a browser.
It is part of the **WebService** and is turned off by default.

What people can do once logged in depends on their role:

| Role | What they can do |
|:-----|:-----------------|
| Admin | Edit node **and** server configurations |
| DCS Admin | Edit server configurations only |
| `managed_by` roles (see servers.yaml) | See and manage only the servers assigned to them (useful for hosters) |

**The setup has three parts:**

1. **Steps 0–2:** turn the frontend on and log in on your own machine or network.
2. **Step 3 (optional):** put it on the internet safely, behind HTTPS, using nginx. If you prefer Caddy, see [Step 4](#step-4-alternative-caddy-instead-of-nginx).
3. **Troubleshooting:** a table of common problems and fixes at the end.

> [!NOTE]
> Commands in this guide are written for **Debian / Ubuntu** (`apt`, `ufw`). On Fedora, RHEL, Rocky or Alma, use `dnf` instead of `apt`, `firewall-cmd` instead of `ufw`, and put nginx configs in `/etc/nginx/conf.d/` (see the notes in Step 3).

---

## Step 0 – Pick your login method

There are two ways for people to log in. Pick **one**:

| | Option A: Discord login | Option B: Local users |
|:--|:--|:--|
| How people log in | With their Discord account | With a username/password you create |
| Needs | A domain name with **HTTPS** (e.g. `https://bot.example.com`), see [Step 3](#step-3-serve-it-on-the-internet-with-https-nginx-reverse-proxy) | Nothing extra; plain HTTP works on your LAN |
| Roles come from | Your Discord roles (via the `roles:` mapping in `services/bot.yaml`) | The roles you write next to each user |
| Recommended for | Communities with several admins | Single admin, testing, LAN-only setups |

> [!NOTE]
> Discord only allows its login to send people back to an `https://` address, which is why Option A needs HTTPS.
> If you want Option A, do Step 3 first (or at least alongside), because Discord login won't work until HTTPS does.

All bot commands below are run in a **terminal, as the user that runs the bot, from your DCSServerBot folder**, for example:

```bash
cd ~/DCSServerBot
```

They use the bot's own Python, which lives in a hidden folder in your home directory, normally `~/.dcssb-<node name>`, where the node name defaults to your hostname. Check where yours is:

```bash
ls -d ~/.dcssb*
```

The commands below assume `~/.dcssb-$(hostname)`. If `ls` showed a different name, use that instead.

---

## Step 1 – Generate a session secret (both options)

The session secret is a random password the website uses to sign login cookies. You only need it once, and nobody types it in.

```bash
~/.dcssb-$(hostname)/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the long line it prints. You'll paste it into `webservice.yaml` below.

> [!CAUTION]
> Treat the session secret like a password. Anyone who has it can forge logins.

---

## Option A – Discord login (HTTPS)

### A1. Set up OAuth2 in the Discord Developer Portal

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select **your bot's application**.
2. Go to **OAuth2**.
3. Copy the **Client ID**.
4. Copy the **Client Secret**. You may have to press **Reset Secret** to see it.
5. Under **Redirects**, press **Add Redirect** (or "Add another") and enter:
   ```
   https://your-dns-name/auth/discord/callback
   ```
   Replace `your-dns-name` with your real domain. It must match `public_base_url` in the next step **exactly**: same `https://`, same spelling, no trailing slash.
6. Press **Save Changes**.

### A2. Edit `config/services/webservice.yaml`

Create the file if it doesn't exist. Replace everything in `<...>` with your values, and keep the quotes.

```yaml
# config/services/webservice.yaml
DEFAULT:
  listen: 127.0.0.1         # only the reverse proxy on this machine can reach the bot (see Step 3)
  port: 9876
  debug: false
  frontend: true            # THIS ENABLES THE FRONTEND
  session:
    secret: '<your session secret from Step 1>'
    https_only: true        # correct for HTTPS
  auth:
    public_base_url: https://your-dns-name   # no trailing slash; must match the Discord redirect
    discord:
      enabled: true
      client_id: '<your client id>'
      client_secret: '<your client secret>'
```

Then continue with **Step 2** and **Step 3**.

---

## Option B – Local users (HTTP)

### B1. Create a user

Run this for each person who should have access, changing the username and role:

```bash
~/.dcssb-$(hostname)/bin/python -m services.webservice.auth.local --hash --username "MyFancyUser" --roles Admin
```

- It asks for the password twice. Nothing is shown while you type; that's normal.
- It then prints a small YAML block containing a `password_hash`. You'll copy the hash into the file below.
- Valid roles are `Admin` and `DCS Admin`. For `DCS Admin`, keep the quotes: `--roles "DCS Admin"`.

### B2. Edit `config/services/webservice.yaml`

Create the file if it doesn't exist. Replace everything in `<...>` with your values.

```yaml
# config/services/webservice.yaml
DEFAULT:
  listen: 0.0.0.0           # 0.0.0.0 = reachable from other machines on your network; use 127.0.0.1 for this machine only
  port: 9876
  debug: false
  frontend: true            # THIS ENABLES THE FRONTEND
  session:
    secret: '<your session secret from Step 1>'
    https_only: false       # MUST be false when using plain HTTP, or logins won't stick
  auth:
    public_base_url: http://<your-ip>:9876    # the address people type in their browser
    local:
      enabled: true
      users:
        - username: MyFancyUser
          password_hash: '<hash from B1>'
          roles: [Admin]
        - username: AnotherUser
          password_hash: '<hash from B1>'
          roles: [DCS Admin]
```

Add one `- username:` block per person. The `- username`, `password_hash` and `roles` lines must stay lined up exactly as shown, because YAML is picky about spaces.

> [!WARNING]
> With plain HTTP, passwords travel unencrypted. Only use this on a network you trust (LAN or VPN).
> To use local users over the internet, do Step 3 and switch to the HTTPS settings shown there.

---

## Step 2 – Restart and open the website

1. Restart DCSServerBot.
2. Open the website on the bot machine: <http://localhost:9876/>. On a headless server, test with `curl -I http://localhost:9876/` instead; any HTTP response means it's up.
3. Log in with your username/password (Option B). Option A users can't log in yet; continue with Step 3.

**Option B only:** to reach it from another machine on your network at `http://<bot-machine-ip>:9876/`, allow the port through the firewall. Limit it to your LAN, adjusting the subnet to yours:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 9876 proto tcp
```

> [!CAUTION]
> **Never forward port 9876 on your router, and never open it to the whole internet** (`ufw allow 9876` without `from`). If you want access from outside your network, use Step 3.

---

## Step 3 – Serve it on the internet with HTTPS (nginx reverse proxy)

A **reverse proxy** is a small web server that sits in front of the bot. Browsers connect to it over HTTPS on the normal web ports (80/443), and it passes the traffic on to the bot on `127.0.0.1:9876`, a port that is never exposed to the outside world.

```
Browser ──HTTPS (443)──▶ nginx (on the bot machine) ──HTTP──▶ DCSServerBot (127.0.0.1:9876)
                                                              not reachable from outside
```

This step uses **nginx** for the proxy and **certbot** for a free, auto-renewing Let's Encrypt certificate.

> [!TIP]
> **Prefer Caddy?** Caddy handles certificates by itself and needs less setup. Do 3.1, 3.3 and 3.7 below, then follow [Step 4](#step-4-alternative-caddy-instead-of-nginx) instead of 3.2, 3.4, 3.5 and 3.6.

### 3.1 What you need

| Requirement | Why | How |
|:--|:--|:--|
| A domain or subdomain, e.g. `bot.example.com` | Certificates are issued for names, not IPs | Any registrar. A subdomain of a domain you already own is fine |
| A DNS **A record** pointing that name to your **public** IP | So browsers find your server | In your DNS provider's panel. Find your public IP with `curl -4 ifconfig.me` |
| Dynamic DNS, if your IP changes | Keeps the A record correct | Use your DNS provider's dynamic DNS feature or a DDNS updater such as `ddclient` |
| Ports **80** and **443** reachable from the internet | 80 is used to get and renew the certificate, 443 to serve the site | Home server: forward TCP 80 and 443 on your router to the machine's LAN IP. VPS: also open them in your provider's cloud firewall, if it has one |
| Local firewall allowing 80 and 443 | Same reason | See 3.3 |

> [!NOTE]
> **Using Cloudflare for DNS?** Set the record to **DNS only** (grey cloud) for the first setup, so the certificate request reaches your server directly.
> You can switch to **Proxied** (orange cloud) afterwards, but then set Cloudflare's SSL/TLS mode to **Full (strict)**.

> [!NOTE]
> **Can't forward ports** (some ISPs, mobile/Starlink connections, CGNAT)? Then a direct reverse proxy won't be reachable from outside.
> Your options are a VPN between you and the bot machine, or a tunnel service. Both are outside the scope of this guide.

### 3.2 Install nginx and certbot

```bash
sudo apt update
sudo apt install -y nginx certbot
sudo systemctl enable --now nginx
```

Remove the default "Welcome to nginx" site so it doesn't get in the way:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

Create the folder certbot will use to prove you own the domain:

```bash
sudo mkdir -p /var/www/certbot
```

### 3.3 Open the firewall for 80 and 443

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

> [!IMPORTANT]
> If `ufw status` says **inactive**, make sure SSH is allowed **before** you enable it, or you can lock yourself out of a remote server:
> ```bash
> sudo ufw allow OpenSSH
> sudo ufw enable
> ```

If you added the LAN rule for port 9876 in Step 2 and no longer need it, remove it:

```bash
sudo ufw delete allow from 192.168.1.0/24 to any port 9876 proto tcp
```

**Fedora / RHEL:** use `sudo firewall-cmd --permanent --add-service=http --add-service=https` and then `sudo firewall-cmd --reload`.

### 3.4 Phase 1 nginx config (HTTP only, to get the certificate)

nginx refuses to start if a config points to certificate files that don't exist yet. So the setup happens in two phases: first HTTP only, to get the certificate, then HTTPS.

Create `/etc/nginx/sites-available/dcsserverbot` (for example with `sudo nano /etc/nginx/sites-available/dcsserverbot`) with this content, using your own domain:

```nginx
# /etc/nginx/sites-available/dcsserverbot  (PHASE 1: HTTP only)
server {
    listen 80;
    listen [::]:80;
    server_name bot.example.com;

    # Let's Encrypt validation files (written by certbot)
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Everything else goes to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}
```

Enable it, test, and reload:

```bash
sudo ln -s /etc/nginx/sites-available/dcsserverbot /etc/nginx/sites-enabled/dcsserverbot
sudo nginx -t
sudo systemctl reload nginx
```

`sudo nginx -t` must say `syntax is ok` and `test is successful`. If it doesn't, fix the error it names before reloading.

**Fedora / RHEL:** there is no `sites-available`. Save the file as `/etc/nginx/conf.d/dcsserverbot.conf` instead and skip the `ln -s` line.

### 3.5 Get the certificate with certbot

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d bot.example.com --deploy-hook "systemctl reload nginx"
```

It asks for your email (for expiry warnings) and to accept the terms. When it's done, your certificate is in:

```
/etc/letsencrypt/live/bot.example.com/fullchain.pem   ← certificate + chain
/etc/letsencrypt/live/bot.example.com/privkey.pem     ← private key
```

The certbot package installs a timer that renews the certificate automatically and reloads nginx afterwards, thanks to `--deploy-hook`. Check that renewal will work with:

```bash
sudo certbot renew --dry-run
```

You don't need to do anything else for renewals; just keep port 80 open.

### 3.6 Phase 2 nginx config (HTTPS)

Replace the whole content of `/etc/nginx/sites-available/dcsserverbot` (Fedora/RHEL: `/etc/nginx/conf.d/dcsserverbot.conf`) with the full config, using your own domain:

```nginx
# /etc/nginx/sites-available/dcsserverbot  (PHASE 2: HTTPS)

# Needed for WebSocket connections, if the frontend uses them
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# Port 80: certificate renewals + redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name bot.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# Port 443: the actual website, passed through to DCSServerBot
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name bot.example.com;

    ssl_certificate      /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key  /etc/letsencrypt/live/bot.example.com/privkey.pem;
    ssl_protocols        TLSv1.2 TLSv1.3;

    location / {
        proxy_pass          http://127.0.0.1:9876;
        proxy_http_version  1.1;
        proxy_set_header    Host               $host;
        proxy_set_header    X-Real-IP          $remote_addr;
        proxy_set_header    X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto  $scheme;
        proxy_set_header    Upgrade            $http_upgrade;
        proxy_set_header    Connection         $connection_upgrade;
    }
}
```

Test and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

> [!NOTE]
> **Fedora / RHEL with SELinux:** nginx isn't allowed to connect to the bot by default, and you'll get **502 Bad Gateway**. Allow it once with:
> ```bash
> sudo setsebool -P httpd_can_network_connect 1
> ```

### 3.7 Point the bot at the proxy

In `config/services/webservice.yaml`, the bot must listen **only locally** and know its public HTTPS address.

**Option A (Discord login):** use the A2 file exactly as shown; it is already set up for this.

**Option B (local users) over the internet:** change your B2 file to this:

```yaml
# config/services/webservice.yaml
DEFAULT:
  listen: 127.0.0.1         # only the reverse proxy on this machine can reach the bot
  port: 9876
  debug: false
  frontend: true            # THIS ENABLES THE FRONTEND
  session:
    secret: '<your session secret from Step 1>'
    https_only: true        # true now, because browsers connect over HTTPS
  auth:
    public_base_url: https://bot.example.com  # your domain, no trailing slash, no port
    local:
      enabled: true
      users:
        - username: MyFancyUser
          password_hash: '<hash from B1>'
          roles: [Admin]
        - username: AnotherUser
          password_hash: '<hash from B1>'
          roles: [DCS Admin]
```

Restart DCSServerBot after saving. Then open `https://bot.example.com` from your phone **on mobile data** (not your Wi-Fi). That proves it works from the outside.

### 3.8 nginx starts with the system

The `systemctl enable --now nginx` from 3.2 already makes nginx start at boot. Useful commands for later:

| Task | Command |
|:--|:--|
| Check the config for errors | `sudo nginx -t` |
| Apply config changes | `sudo systemctl reload nginx` |
| Restart nginx | `sudo systemctl restart nginx` |
| See whether nginx is running | `systemctl status nginx` |
| Read the error log | `sudo tail -n 50 /var/log/nginx/error.log` |
| List certificates and expiry dates | `sudo certbot certificates` |

---

## Step 4 – Alternative: Caddy instead of nginx

[Caddy](https://caddyserver.com/) gets and renews HTTPS certificates **by itself**, so it replaces both nginx and certbot. Do **3.1, 3.3 and 3.7** from Step 3, then:

1. Install Caddy. On Debian 12+ and Ubuntu 24.04+ it's in the standard repositories:

   ```bash
   sudo apt update
   sudo apt install -y caddy
   ```

   For other or older distributions, follow <https://caddyserver.com/docs/install>. The package sets Caddy up as a service that starts at boot.

2. Replace the whole content of `/etc/caddy/Caddyfile` with this, using your domain:

   ```
   # /etc/caddy/Caddyfile
   bot.example.com {
       reverse_proxy 127.0.0.1:9876
   }
   ```

3. Apply it and watch the log:

   ```bash
   sudo systemctl reload caddy
   sudo journalctl -u caddy -f
   ```

   Within a minute it should report that it obtained a certificate for your domain. Stop watching with `Ctrl+C`, then check `https://bot.example.com` from mobile data.

After editing the Caddyfile later, run `sudo systemctl reload caddy` again.

> [!NOTE]
> Don't run nginx and Caddy at the same time; both want ports 80 and 443. If you tried nginx first, stop it with `sudo systemctl disable --now nginx`.

---

## Security notes

- **Only ports 80 and 443 should ever be reachable from the internet**, and only via the reverse proxy. Never forward or open 9876 or any other DCSServerBot port.
- Keep `webservice.yaml` private. It contains your session secret, client secret and password hashes. Lock it down with `chmod 600 config/services/webservice.yaml`.
- Use strong passwords for local users; anyone on the internet can reach the login page.
- Use `debug: false` unless you're actively troubleshooting.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|:--|:--|:--|
| `No such file or directory` for `~/.dcssb-…/bin/python` | The bot's environment has a different name, or you're logged in as a different user than the one running the bot | Run `ls -d ~/.dcssb*` as the bot's user and use that path |
| `No module named services` | Command not run from the DCSServerBot folder | `cd` into the DCSServerBot folder first |
| Page loads on the bot machine but not from other machines (LAN) | Firewall, or `listen: 127.0.0.1` | Add the LAN `ufw` rule from Step 2; use `listen: 0.0.0.0` (LAN setups only) |
| Login seems to work, then you're sent straight back to the login page | `https_only` doesn't match how you connect | `false` for plain HTTP, `true` behind HTTPS |
| Discord shows **Invalid OAuth2 redirect_uri** | Redirect in the Developer Portal ≠ `public_base_url` + `/auth/discord/callback` | Make them match character for character (`https`, domain, no port, no trailing slash) |
| Logged in but can't edit anything | Your account has no Admin / DCS Admin role | Option A: check the `roles:` mapping in `services/bot.yaml`. Option B: check `roles:` for your user |
| Bot fails to start after editing the file | YAML indentation or unquoted special characters | Compare your file line by line with the example; keep secrets in quotes |
| `nginx: [emerg] cannot load certificate` | Phase 2 config used before the certificate exists, or a typo in the domain in the path | Go back to the Phase 1 config, finish 3.5, check `sudo ls /etc/letsencrypt/live/` |
| `bind() to 0.0.0.0:80 failed (98: Address already in use)` | Another program is using port 80 or 443 (Apache, Caddy, another nginx…) | Find it with `sudo ss -tlnp \| grep -E ':80\|:443'` and stop or disable it |
| certbot says the challenge failed | Port 80 not reachable, DNS not pointing to your public IP yet, Cloudflare proxy (orange cloud) on, or the Phase 1 config not active | Check port forwarding / cloud firewall; check the record with `dig +short bot.example.com`; set Cloudflare to "DNS only"; run `sudo nginx -t` and reload |
| Site works on your network but not from outside (or the reverse) | Your router doesn't support "NAT loopback", or port forwarding is missing | Always test from mobile data. If outside works, the setup is fine |
| **502 Bad Gateway** | nginx can't reach the bot | Make sure the bot is running and that the `port:` in webservice.yaml matches `proxy_pass` (9876). On Fedora/RHEL, see the SELinux note in 3.6 |
| Browser shows a certificate warning | The certificate doesn't match the name, or Cloudflare SSL mode is wrong | Use exactly the domain from your nginx config; with Cloudflare proxy on, use **Full (strict)** |
| Changes to the nginx config have no effect | nginx wasn't reloaded, or you edited the wrong file | `sudo nginx -t && sudo systemctl reload nginx`; check that `sites-enabled/dcsserverbot` points to your file with `ls -l /etc/nginx/sites-enabled/` |
