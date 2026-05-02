# ulauncher-protondb

Search Steam games and check their [ProtonDB](https://www.protondb.com) Linux compatibility rating from [Ulauncher](https://ulauncher.io).

## Installation

This extension requires `requests` and `beautifulsoup4`. Ulauncher does not install dependencies automatically, so install them first.

On Arch Linux:
```bash
sudo pacman -S python-requests python-beautifulsoup4
```

On other distributions:
```bash
pip install --user requests beautifulsoup4
```

Then open Ulauncher preferences → Extensions → Add extension and paste:

```
https://github.com/dru89/ulauncher-protondb
```

## Usage

Trigger with the `proton` keyword (configurable):

```
proton elden ring
proton baldurs gate
proton hades
```

Results show the ProtonDB tier alongside ownership and install status:

```
💎 Platinum  ·  847 reports  ·  ✓ Installed
🥇 Gold  ·  312 reports  ·  In library
🥈 Silver  ·  54 reports
❓ Pending  ·  3 reports
```

Selecting a result opens an action menu with context-aware options:

- **Launch game** — opens via `steam://run/{id}` (installed games only)
- **Install game** — opens Steam install dialog (owned but not installed)
- **Open on ProtonDB** — full report page with user notes
- **Open on Steam** — Steam store page
- **Copy App ID** — copies the Steam App ID

## Rating tiers

| Emoji | Tier | Meaning |
|-------|------|---------|
| 💎 | Platinum | Works perfectly out of the box |
| 🥇 | Gold | Works great with minor tweaks |
| 🥈 | Silver | Some workarounds required |
| 🥉 | Bronze | Runs but with significant issues |
| 💀 | Borked | Doesn't run |
| ❓ | Pending | Not enough reports yet |
| — | Not on ProtonDB | No entry in the ProtonDB database |

## Preferences

| Preference | Default | Description |
|------------|---------|-------------|
| Keyword | `proton` | Trigger keyword |
| Max results | 8 | Number of games to show (5–10) |
| Minimum rating | Any | Hide games below this tier |
| Rating cache TTL | 24 hours | How long to keep cached ratings |
| Steam API key | *(optional)* | Enables library pre-warming for all owned games |
| Steam ID | *(optional)* | Your 64-bit Steam ID |

## Library integration

Without Steam credentials, the plugin detects **installed** games by scanning your local Steam library (`~/.local/share/Steam/steamapps/`). This works automatically with no configuration.

With a **Steam API key** and **Steam ID**, the plugin also fetches your full owned-games list and pre-warms ProtonDB ratings for everything in your library — so results for games you own are nearly instant even before you search for them.

Get a Steam API key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey). Find your Steam ID at `steamcommunity.com/id/yourusername` (look for the 17-digit number in the URL or page source).

## Caching

ProtonDB ratings are cached locally in `~/.cache/ulauncher-protondb/ratings.db`. Game capsule images are cached in `~/.cache/ulauncher-protondb/images/`. The cache TTL is configurable (default 24 hours).
