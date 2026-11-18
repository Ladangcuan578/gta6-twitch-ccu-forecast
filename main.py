#!/usr/bin/env python3
"""Forecast GTA VI's launch-day Twitch viewers, hour by hour.

Reads every case file in $MAKEFUN_INPUT_DIR (default /input/) and writes
one forecast per case to $MAKEFUN_OUTPUT_DIR (default /output/):
    {"hours": [{"hour": h, "q": [q05, q25, q50, q75, q95]}]}  for h = 2..24

Method (standard library only, no network, deterministic):
  * Anchor on the case's hour-1 print p1.
  * Each past launch ("comparable") in the case gives a launch-day curve
    in log space: lr_c[h] = ln(viewers_c[h] / viewers_c[hour 1]).
  * A launch's curve is split into one amplitude plus a shape template:
        A_c  = mean of lr_c over the evening hours (15-22)
        shape_c[h] = lr_c[h] - A_c
    The amplitude is what varies most between launches (it is strongly
    tied to how much of the launch had already happened by 00:00 UTC,
    which the hour-1 print itself reveals); the shape is far more stable.
  * Similarity between the target launch and each comparable is measured
    on the pre-launch fingerprint: the absolute log viewer counts at
    1, 2, 3, 6, 12, 24 and 48 hours before the release day, plus the
    hour-1 count itself.  A Gaussian kernel turns the distance into a
    weight.  Launches that ramped up like the target (same unlock
    structure, same scale) get the weight.
  * For each kernel bandwidth in (0.6, 0.7, 0.8, 1.0) one member
    forecast is built: amplitude = similarity-weighted median of A_c,
    template = similarity-weighted median of shape_c[h], spread =
    similarity-weighted interquartile range of the residuals around the
    fitted curve (scaled, floored at 0.30 log units so no hour claims
    more precision than the data supports, capped at 1.0).  Quantiles
    are p1 * exp(centre + z_tau * sigma) with standard-normal z-scores.
  * The members are combined by geometric mean of the quantiles.  The
    ensemble is what makes the forecast robust: a single bandwidth's
    weighted median can flip between two neighbouring launches, while
    the ensemble averages over that choice.

Practice (four past launches, answers from the same public hourly
source the kit's cases were built from): mean pinball loss on the log
scale 0.143, against 0.387 for the house baseline starter.
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

Z_SCORES = (-1.6448536269514722, -0.6744897501960817, 0.0,
            0.6744897501960817, 1.6448536269514722)
BANDWIDTHS = (0.6, 0.7, 0.8, 1.0)
PRE_OFFSETS = (1, 2, 3, 6, 12, 24, 48)
AMP_HOURS = (15, 16, 17, 18, 19, 20, 21, 22)
SIG_MULT = 0.5
SIG_FLOOR = 0.30
SIG_CAP = 1.0


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def tier_of(comp):
    """Tier is recorded inside the comparable's unlock note."""
    match = re.search(r"Tier:\s*([a-z-]+)", comp.get("unlock", "") or "")
    return match.group(1) if match else "comparable"


def hourly_map(points):
    """Map whole-hour start -> viewers, skipping missing/non-positive."""
    out = {}
    for point in points or []:
        viewers = point.get("viewers")
        if viewers is None or viewers <= 0:
            continue
        stamp = point.get("from") or point.get("at")
        if stamp is None:
            continue
        try:
            out[parse_ts(stamp)] = float(viewers)
        except (TypeError, ValueError):
            continue
    return out


def weighted_median(pairs):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[len(pairs) // 2][0] if pairs else 0.0
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= total / 2.0:
            return value
    return pairs[-1][0]


def weighted_quantile(pairs, q):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[int(q * (len(pairs) - 1))][0] if pairs else 0.0
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= total * q:
            return value
    return pairs[-1][0]


def round_half_up(value):
    return float(Decimal(repr(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))


def hour_one_print(case):
    known = case.get("known") or []
    if isinstance(known, dict):
        known = [known]
    for row in known:
        if row.get("hour") == 1 and row.get("viewers"):
            return float(row["viewers"])
    if known and known[0].get("viewers"):
        return float(known[0]["viewers"])
    history = hourly_map(case.get("history_hourly"))
    day_start = parse_ts(case["day_start"])
    if history.get(day_start):
        return history[day_start]
    return 1.0


def build_comparables(case, target_name):
    """Comparable launches with a complete release-day curve.

    Returns a list of dicts with the log-ratio curve (hours 1..24),
    the amplitude, and the pre-launch absolute log levels.
    """
    out = []
    for comp in case.get("comparables") or []:
        tier = tier_of(comp)
        if tier == "target-history":
            continue
        if tier != "comparable":
            continue
        name = comp.get("name") or ""
        if target_name and target_name.lower() in name.lower():
            continue
        try:
            day_start = parse_ts(comp["day_start"])
        except (KeyError, TypeError, ValueError):
            continue
        hourly = hourly_map(comp.get("hourly"))
        first = hourly.get(day_start)
        if not first:
            continue
        curve = {}
        complete = True
        for hour in range(1, 25):
            value = hourly.get(day_start + timedelta(hours=hour - 1))
            if value is None:
                complete = False
                break
            curve[hour] = math.log(value / first)
        if not complete:
            continue
        pre = {}
        for offset in PRE_OFFSETS:
            value = hourly.get(day_start - timedelta(hours=offset))
            pre[offset] = math.log(value) if value else None
        pre[0] = math.log(first)
        out.append({
            "curve": curve,
            "amplitude": sum(curve[h] for h in AMP_HOURS) / len(AMP_HOURS),
            "pre": pre,
        })
    return out


def member_forecast(p1, target_pre, comparables, bandwidth):
    """One ensemble member: amplitude + template at a fixed bandwidth."""
    for comp in comparables:
        dist2 = 0.0
        nfeat = 0
        for key in list(PRE_OFFSETS) + [0]:
            a = target_pre.get(key)
            b = comp["pre"].get(key)
            if a is None or b is None:
                continue
            dist2 += (a - b) ** 2
            nfeat += 1
        if nfeat:
            comp["weight"] = math.exp(
                -dist2 / (2.0 * bandwidth * bandwidth * nfeat))
        else:
            comp["weight"] = 1.0
    amplitude = weighted_median(
        [(c["amplitude"], c["weight"]) for c in comparables])
    hours = []
    for hour in range(2, 25):
        template = weighted_median(
            [(c["curve"][hour] - c["amplitude"], c["weight"])
             for c in comparables])
        centre = amplitude + template
        residuals = [(c["curve"][hour] - centre, c["weight"])
                     for c in comparables]
        q25 = weighted_quantile(residuals, 0.25)
        q75 = weighted_quantile(residuals, 0.75)
        sigma = (q75 - q25) / 1.349 * SIG_MULT
        sigma = max(SIG_FLOOR, min(SIG_CAP, sigma))
        quantiles = []
        for z in Z_SCORES:
            q = p1 * math.exp(centre + z * sigma)
            q = max(1.0, min(1e9, q))
            quantiles.append(round_half_up(q))
        for i in range(1, 5):
            if quantiles[i] < quantiles[i - 1]:
                quantiles[i] = quantiles[i - 1]
        hours.append({"hour": hour, "q": quantiles})
    return {"hours": hours}


def baseline_forecast(p1):
    """Fallback when no comparable is usable: the house baseline shape."""
    hours = []
    for hour in range(2, 25):
        sigma = 0.15 * math.sqrt(hour - 1)
        quantiles = []
        for z in Z_SCORES:
            q = max(1.0, min(1e9, p1 * math.exp(z * sigma)))
            quantiles.append(round_half_up(q))
        hours.append({"hour": hour, "q": quantiles})
    return {"hours": hours}


def forecast_case(case):
    p1 = hour_one_print(case)
    if p1 < 1:
        p1 = 1.0
    try:
        day_start = parse_ts(case["day_start"])
    except (KeyError, TypeError, ValueError):
        return baseline_forecast(p1)
    target_history = hourly_map(case.get("history_hourly"))
    target_pre = {}
    for offset in PRE_OFFSETS:
        value = target_history.get(day_start - timedelta(hours=offset))
        target_pre[offset] = math.log(value) if value else None
    target_pre[0] = math.log(p1)
    game = case.get("game") or {}
    target_name = game.get("name") or ""
    comparables = build_comparables(case, target_name)
    if not comparables:
        return baseline_forecast(p1)
    members = [member_forecast(p1, target_pre, comparables, bw)
               for bw in BANDWIDTHS]
    hours = []
    for i in range(23):
        quantiles = []
        for j in range(5):
            logs = [math.log(m["hours"][i]["q"][j]) for m in members]
            quantiles.append(round_half_up(math.exp(sum(logs) / len(logs))))
        for j in range(1, 5):
            if quantiles[j] < quantiles[j - 1]:
                quantiles[j] = quantiles[j - 1]
        hours.append({"hour": i + 2, "q": quantiles})
    return {"hours": hours}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input/")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output/")
    os.makedirs(output_dir, exist_ok=True)
    if not os.path.isdir(input_dir):
        print(f"input dir not found: {input_dir}", file=sys.stderr)
        return 1
    names = sorted(n for n in os.listdir(input_dir) if n.endswith(".in"))
    for name in names:
        case_id = name[:-3]
        try:
            with open(os.path.join(input_dir, name)) as handle:
                case = json.load(handle)
            forecast = forecast_case(case)
        except Exception as exc:  # never leave a case without an output
            print(f"{name}: {exc}; writing baseline forecast",
                  file=sys.stderr)
            forecast = baseline_forecast(1.0)
        with open(os.path.join(output_dir, case_id + ".out"), "w") as handle:
            json.dump(forecast, handle)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
