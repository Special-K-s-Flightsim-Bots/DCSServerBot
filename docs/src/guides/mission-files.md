# Mission files and the .dcssb directory

When a tool modifies a mission — MizEdit, RealWeather, `/mission modify` — the bot cannot simply
write the file: the running DCS process holds it open. So it works on copies, and it keeps the
original.

## What happens to a mission

1. **First change.** The original mission is copied to `Missions/.dcssb/<name>.miz.orig`.
2. **Write.** The changed mission goes to `Missions/.dcssb/<name>.miz`.
3. **Load.** The server loads the copy from `.dcssb/`.
4. **Second change.** The next modification writes to the original name again, and so on — the bot
   alternates between the two files so that one of them is always free.

## The .orig file

`.dcssb/<name>.miz.orig` is the mission as you uploaded it, before any modification. It is your
rollback: deleting the modified copies and loading again restores what you started with.

By default every modification starts from the `.orig` file, so applying a preset twice produces the
same result rather than stacking. All modify commands take a `use_orig` option — set it to `false` to
build on the last modified file instead.

## What the bot handles for you

- `/mission upload` replaces the mission and resets the `.orig`
- `/mission load` and `/download <Missions>` always give you the file that is actually running
- `.dcssb` is on the ignore list for mission uploads and autoscan, so the copies never appear as
  missions themselves

Nothing in `.dcssb` needs backing up separately: [Backup & restore](backup-restore.md) covers the
missions directory, and the `.orig` files are recreated from the missions you upload.
