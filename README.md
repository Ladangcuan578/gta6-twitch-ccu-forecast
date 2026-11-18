# GTA VI launch-day Twitch viewers — hour-by-hour forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Ladangcuan578/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Ladangcuan578/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entry for the [make.fun bounty `gta6-twitch-ccu-forecast-01`](https://make.fun/bounty/gta6-twitch-ccu-forecast-01): forecast, for
each of hours 2–24 (UTC) of GTA VI's release day (2026-11-19), five
quantiles of the Twitch concurrent viewers in the "Grand Theft Auto VI"
category, from everything recorded up to the end of hour 1.

## Run

```sh
python3 main.py            # reads $MAKEFUN_INPUT_DIR (default /input/)
                           # writes $MAKEFUN_OUTPUT_DIR (default /output/)
```

Python 3.13, standard library only, no network, deterministic. One
entrypoint: `main.py`. It processes every `*.in` file found and writes one
`<id>.out` per case:

```json
{"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]}, ..., {"hour": 24, "q": [...]}]}
```

## Method

Anchored on the case's hour-1 print, the program compares the target
launch with the past launches ("comparables") shipped in the case file:

- Each comparable's launch-day curve is written in log space, as
  log-ratios to its own hour-1 print, and split into one **amplitude**
  (mean log-ratio over the evening hours 15–22 — what varies most
  between launches) plus a **shape template** (the rest of the curve).
- Similarity is measured on the pre-launch fingerprint: the absolute log
  viewer counts 1, 2, 3, 6, 12, 24 and 48 hours before the release day,
  plus the hour-1 count. A Gaussian kernel turns the distance into a
  weight, so launches that ramped up like the target (same unlock
  structure, same scale) dominate.
- Four ensemble members (kernel bandwidths 0.6, 0.7, 0.8, 1.0) each
  forecast amplitude (similarity-weighted median), template
  (similarity-weighted median) and spread (similarity-weighted
  interquartile range of the residuals, scaled by 0.5, floored at 0.30
  log units, capped at 1.0). Quantiles are
  `p1 * exp(centre + z_tau * sigma)` with standard-normal z-scores.
  Members are combined by geometric mean of the quantiles.

Only "comparable"-tier launches are used; the target's own pre-release
history and early-access-contaminated / weak launches are excluded. If no
comparable is usable, the program falls back to the house baseline shape
(flat median, spread widening as `0.15 * sqrt(h - 1)`).

## Local validation

Practice answers were read from the same public hourly source the kit's
cases were built from (SullyGnome "Average viewers" for each practice
launch's release day) and verified against each case's hour-1 print and
preceding history hours. Mean pinball loss on the log scale, per the
rules:

| case | loss |
| ---- | ---- |
| p00 Marvel's Wolverine | 0.202 |
| p01 Red Dead Redemption 2 | 0.104 |
| p02 God of War Ragnarök | 0.093 |
| p03 Marvel's Spider-Man 2 | 0.172 |
| **overall** | **0.143** |

House baseline starter on the same cases: 0.387. Runtime ~0.05 s per
case; repeated runs are byte-identical.

---

<sub>Entered on [make.fun](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
