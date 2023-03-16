---
title: Create Discord Bot
parent: Installation
nav_order: 4
---

# Create a Discord Bot

The bot needs a unique Token for each installation:
- Open the website [Discord for Developers]
- Click on the button `New Application`
- Give it a Name, e.g. **DCSServerBot**
- Agree the Terms of Service
- Click on the button `Create`

Now you see the general information of your new App.
- You can add a description, some tags and an icon (all optional)
- Select `Bot` from the left menu, click on the `Add Bot` button and confirm the action
- Enter 2FA code if requested
- Click on `Copy` below **TOKEN** and save it in your passwort manager

{: .important }
> You need to add the Token to the `dcsserverbot.ini` in your config directory later!
> It can only be copied once! If you don't save it, you have to regenerate a new one.

- You see three toggle switches under `Privileged Gateway Intents` which must activated:
  - Presence Intent
  - Server Members Intent
  - Message Content Intent

# Add the bot to your Discord server / guild

- Select **OAuth2** from the left menu and **URL Generator**
- In the SCOPES select the **bot** checkbox
- In the Bot Permissions section activate the following:
  - Manage Channels
  - Send Messages
  - Manage Messages
  - Embed Links
  - Attach Files
  - Read Message History
  - Add Reactions
- Press `Copy` on the generated URL and paste it into the browser of your choice
- Select the guild / Dicord server the bot has to be added to and confirm the permissions
- This will add the bot to the selected server

{: .important }
> This will create a role with the same name and add the bot to it.
> The role will have the selected permissions server wide.

[Discord for Developers]: http://discord.com/developers
