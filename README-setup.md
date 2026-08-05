# canval online — what to put where

Three files, one setting, two secrets. No server, no card, nothing left
running on your machine.

## 1. Copy the files into the repo

    canval/export_site.py              new module
    .github/workflows/canval-nightly.yml   the scheduled job
    docs/index.html                    the page itself
    docs/data/*.json                   sample data, replaced on first run

## 2. Turn on Pages

Settings → Pages → Source: **Deploy from a branch** → Branch **main**,
folder **/docs** → Save.

The address appears there. On a private repository the page is still
reachable by anyone who has the link, so treat the link as the key. If
that is not acceptable, say so and sign-in gets added — Firebase Auth,
also free, about an hour of work.

## 3. Add the secrets

Settings → Secrets and variables → Actions → New repository secret:

    XDM_CLIENT_ID       external_afaqy_client
    XDM_CLIENT_SECRET   the rotated secret
    XDM_DOMAIN          eu

GitHub stores these encrypted; they are masked in logs and cannot be read
back out of the interface.

## 4. Run it once by hand

Actions → **canval nightly** → Run workflow.

The first run has no cached database, so it sweeps all 25089 devices and
takes roughly half an hour. Every run after that reads only what changed
and finishes in a few minutes. When it is done, `docs/data/` holds the
real numbers and the page shows them.

## What still runs locally

The live-readings gate. Checking whether an installed file is actually
reporting needs the Afaqy token, and that stays on your machine with
`canval check` until it is worth adding a proxy for it.
