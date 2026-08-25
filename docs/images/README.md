# Screenshots and diagrams

The README references these by name. Replace a file in place and the README picks it up,
with nothing else to wire up.

| file | what it shows | width |
|---|---|---|
| `library.png` | the main library grid, full of real box art. The hero shot. | 1600 |
| `detail.png` | one game's detail page: matched providers, scores, attributes, tags | 1400 |
| `platforms.png` | the same titles listed once per platform, plus one entry merged across two stores | 1800 |
| `wand.png` | the magic wand dialog, light vs heavy, showing the scope before it runs | 1600 |
| `media.png` | the media classification matrix for one game | 1600 |
| `dashboard.png` | the dashboard: counts, recent arrivals, what needs attention | 1800 |

Captured at a 1440x900 viewport with `deviceScaleFactor: 2`, then downscaled to the widths
above. The script that took them lives outside this repo, because it needs a login.

## Guidelines

- **Dark theme.** It is what the app looks like and it reads well on GitHub.
- **No browser chrome.** No tabs, no URL bar, no OS taskbar.
- **Retina 2x, then downscale.** GitHub scales again and they stay crisp.
- **Real data.** A screenshot of five placeholder games undersells it more than no
  screenshot at all.
- **Under 2 MB each.** Crop the empty floor out of a short result list rather than
  shipping a mostly-blank frame.
- **Nothing private in frame.** No API keys, no host paths, no account names. The
  Settings API-keys panel is off limits even masked.

## Anything else

Extra images are welcome. Add them here and reference them from the relevant doc rather
than the README, which should stay short.
