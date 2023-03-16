---
title: DCSServerBot
parent: Installation
nav_order: 6
---

# Download

You have two options to download the bot:
- Git clone the repository
- Download a zip file

If you want to use the auto update feature you must use git clone:
- Open a git bash in the perent folder, where you want to place the DCSServerBot folder and run this command:
`git clone https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git`
- The folder DCSServerBot will be created automatically and all files will be cloned into this folder

Otherwise download the latest [release version] (see Assets `Source code (zip)`).
Extract it somewhere on the PC that is running the DCS server(s).
Ensure write permissions are given in the target folder.

{: .warning }
> Do not use default folder like `C:\Program Files` or `C:\Program Files (x86)`, because this folders have special protection mechanisms.

{: .warning }
> Make sure that the bots installation directory can only be seen by yourself and is not exposed to anybody outside via www etc., because the Discord bot TOKEN is a secret.

# Installation

Then run the provided install script:
- Double click on the file `install.cmd`
- The script will create a [virtual environment] in the subfolder `venv`
    - This will isolate the dependencies and avoids problems with other Python applications
- All needed requirements will be installed in this venv automatically.
    - If you want to know the needed modules take a look at `requirements.txt`
- After that `install.py` will be started with this virtual environmant.

The install script will execute the following steps:
- Search available DCS installations in the registry
    - If no one is found you will be asked to enter the path manually
    - If you have more then one installation, you need to choose which one should be used
    - Use this value for the setting `DCS_INSTALLATION`
- Ask to enter the Discord Bot `TOKEN`.
- Find a local installed PostgreSQL server
    - Ask for the master password
    - Create user `dcsserverbot` with a randomly generated password
    - Create the database `dcsserverbot`
    - Assign permissions of the user to the database
    - Generate a value for `DATABASE_URL`
- Configure `AUTOUPDATE` if Git is available
- Search for configured server instances:
    - Which are all subfolders of `Saved Games` which contains a `serverSettings.lua`
    - Ask the user which instances should be added
    - Add a section to the ini file for each instance
- Save all these settings in the file `dcsserverbot.ini`

{: .warning }
> If no PostgreSQL server installation is found, `install.py` will exit.
> Then copy `dcsserverbot.ini` from the folder `config\sample` to `config` and cofig the bot manually.

{: .highlight }
> Now a basic version of `dcsserverbot.ini` is stord in the subfolder `config`.
> But this file must be edited to fill in some missing values!

# Last configuration steps

- Open `dcsserverbot.ini` with a text editor, like [notepad++]
- Search all occurences of `<see documentation>` and replace them with the rigt value
    - This will be all needed Discord channels you have created earlier
        - Copy the ID from Discord and past them into the file
    - If no DCS installation is found, you need to add it manually
    - Change value of the setting `OWNER`
        - That is Discord ID of the bots owner (that's you!).
        - If you don't know your ID, go to your Discord profile, make sure "Developer Mode" is enabled under "Advanced", go to "My Account", press the "..." besides your profile picture and select "Copy ID"
- If you want to use custom Role names add the section `ROLES` (see [Prepare Discord Server] for details)

{: .highlight }
> Now it is time to run the bot for the first time!

# Running the bot for the first time

{: .warning }
> Stop all running DCS servers, because some files in the installation directory must be modified!

Start the bot by double click on `rum.cmd`
- This script checks if a [virtual environment] exists. If not it will be created.
- `run.py` will be executed
    - It will perform some checks to see if the configuration is valid and nothing is broken
    - The Database tables will be created
    - Desanitizing of `MissionScripting.lua` will be done
    - DCS World integration is done by adding Hooks to each server instance
    - Some needed fonts are installed, if they are missing
    - Connection to Discord will be established

Start your servers manually and check if the channels are updated.

{: .highlight }
> The bot is working now with a basic configuration.
> But there is a lot more the bot can do for you. Take a look in the configuration and plugin sections.
> We suggest to start with the scheduler plugin, to automatically start and stop your DCS servers by the bot.

[release version]: https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/releases/
[virtual environment]: https://docs.python.org/3/library/venv.html
[notepad++]: https://notepad-plus-plus.org/
[Prepare Discord Server]: ./discord-server.md#create-roles
