# Fonts for CJK player names

Reports and graphs render player names in an image. Without a CJK font, Chinese, Japanese and Korean
names come out as empty boxes.

The fonts cannot be downloaded automatically, so you fetch them once:

1. Open Google Fonts and get one of
   [Noto Sans TC](https://fonts.google.com/noto/specimen/Noto+Sans+TC),
   [Noto Sans JP](https://fonts.google.com/noto/specimen/Noto+Sans+JP) or
   [Noto Sans KR](https://fonts.google.com/noto/specimen/Noto+Sans+KR).
2. Press *Get font* → *Download all*; you get a ZIP.
3. Create a folder `fonts` below the DCSServerBot installation directory and put the ZIP in it.
4. Start the bot. It unpacks the ZIP on the next start and deletes the archive.

From then on the font is used without further configuration.

!!! tip
    One font is enough. Pick the one matching your community's language; the bot does not need all
    three.

The same setting can name a specific font family for a report through `reports.cjk_font` in
`config/services/bot.yaml`.
