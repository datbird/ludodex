# Data licensing and attribution

**The MIT licence in `LICENSE` covers ludodex's CODE. It does not cover game data.**

That distinction matters because MIT permits commercial use and some of the data ludodex
works with does not. This file says which is which.

## The repository ships no third-party data

Nothing in this repo is a copy of anyone's game catalog. ludodex talks to providers using
**credentials you supply** and caches the results into local databases on **your** machine.
Clone it and you have code, not data.

So the licences below apply to what ludodex *fetches* and to the optional published index —
never to the repository itself.

## Providers, and the terms you are agreeing to

You need your own account with each of these. Using ludodex does not grant you rights to
their data; their terms do, and they are between you and them.

| Provider | What it supplies | Terms |
|---|---|---|
| [ScreenScraper.fr](https://www.screenscraper.fr) | Game identities, regional names, ROM hashes, media | **CC BY-NC-SA 4.0** — [licence](https://creativecommons.org/licenses/by-nc-sa/4.0/), [FAQ](https://www.screenscraper.fr/faq.php) |
| [IGDB.com](https://www.igdb.com) (Twitch) | Game identities, names, alternative names, release dates, store ids | [IGDB API terms](https://www.igdb.com/api) |
| [SteamGridDB](https://www.steamgriddb.com) | Artwork | [SteamGridDB terms](https://www.steamgriddb.com/) |
| Steam, GOG, Epic, EA, Xbox, PlayStation | Your own ownership data | Each store's terms |

ScreenScraper is community-funded and rate-limits by contributor tier. If you use it
heavily, [support it](https://www.screenscraper.fr).

## The optional match index

ludodex can use a prebuilt **match index** — a single SQLite file that resolves a store id,
a title or a ROM hash to every other identifier for the same game, offline. It is optional;
ludodex works without it by searching providers directly.

Because it is built from ScreenScraper's database, **it is a derivative work of CC BY-NC-SA
4.0 material**, and all three conditions travel with it:

- **BY** — attribution to ScreenScraper and its contributors. Stamped inside the file, in
  `identity_state`, so it survives being copied away from wherever it was downloaded.
- **NC** — non-commercial use only. It may not be sold, nor bundled into anything sold.
  **This is stricter than ludodex's MIT code licence:** a commercial fork of ludodex is
  permitted by MIT and still may not ship or sell this index.
- **SA** — any redistributed version, modified or not, stays under CC BY-NC-SA 4.0.

The index contains identifiers, names, alternative names, release years and ROM hashes. It
contains no artwork, descriptions, summaries, ratings or provider code — and nothing from
your library: no file paths, no ownership, no credentials, no personal data. It is built
only from the two catalog mirrors.

If you publish your own build, you take on those conditions. If you are unsure whether your
use is commercial, assume it is and don't.

## Building your own instead

`python3 matchindex.py` builds the index locally from mirrors you fetched with your own
credentials. Nothing is redistributed, so only the providers' own terms apply to you.

## Reporting a licensing problem

If you represent one of the providers above and something here misrepresents your terms or
oversteps them, open an issue — it will be corrected or removed.
