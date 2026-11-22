#!/usr/bin/env python3
"""Parse every run in results/raw/ and produce results/summary.csv + results/figures/*.png.

Per run (all times relative to chaos start, t=0):
  failed_requests / error_rate   requests with status != 200 between chaos start and end of run
  outage_s                       first failure -> last failure + one probe interval (0.5s)
  recovery_s                     chaos start -> first successful request after the last failure
  detect_s                       chaos start -> first API pod seen not-ready or deleted (pods.log)
  replacement_ready_s            chaos start -> a *new* API pod (not present before) became Ready
  restarts / oomkilled           container restarts and OOMKilled terminations during the run
  max_api_ready                  most API pods Ready at once (shows HPA scale-out)
  baseline_* / chaos_*           latency of successful requests (ms) before and during chaos
"""
import csv
import glob
import os
import statistics as st
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "results", "raw")
FIG = os.path.join(ROOT, "results", "figures")
INTERVAL = 0.5

# colours (validated reference palette): identity = replica count, red reserved for failures
C1, C3, FAIL, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#e34948", "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150,
    "axes.axisbelow": True,
})


def p95(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def r(x, n=2):
    return None if x is None else round(x, n)


def load_meta(d):
    meta = {}
    with open(os.path.join(d, "meta.env")) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                meta[k] = v
    for k in ("CHAOS_START", "CHAOS_END", "END"):
        meta[k] = float(meta[k])
    return meta


def load_requests(d):
    rows = []
    with open(os.path.join(d, "requests.log")) as f:
        for line in f:
            p = line.split()
            if len(p) >= 3:
                try:
                    rows.append((float(p[0]), p[1], float(p[2])))
                except ValueError:
                    pass
    return sorted(rows)


def load_pods(d):
    """pods.log: epoch EVENT name ready phase restarts node reason"""
    rows = []
    path = os.path.join(d, "pods.log")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                p = line.split()
                if len(p) >= 6:
                    rows.append({"t": float(p[0]), "ev": p[1], "name": p[2], "ready": p[3],
                                 "phase": p[4], "restarts": p[5], "reason": " ".join(p[7:])})
    return rows


def before_pods(d):
    names = set()
    path = os.path.join(d, "pods-before.txt")
    if os.path.exists(path):
        with open(path) as f:
            for line in f.readlines()[1:]:
                if line.startswith("api-"):
                    names.add(line.split()[0])
    return names


def analyse(d):
    m = load_meta(d)
    t0 = m["CHAOS_START"]
    reqs = load_requests(d)
    base = [x for x in reqs if x[0] < t0]
    impact = [x for x in reqs if x[0] >= t0]
    chaos = [x for x in reqs if t0 <= x[0] <= m["CHAOS_END"]]
    fails = [x for x in impact if x[1] != "200"]

    out = {"run": os.path.basename(d), "experiment": m["EXPERIMENT"], "replicas": int(m["REPLICAS"]),
           "run_no": int(m["RUN"]), "hpa": m.get("HPA", "0"), "requests": len(impact),
           "failed_requests": len(fails),
           "error_rate_pct": r(100 * len(fails) / len(impact)) if impact else None}
    if fails:
        out["first_fail_s"] = r(fails[0][0] - t0)
        out["outage_s"] = r(fails[-1][0] - fails[0][0] + INTERVAL)
        after = [x for x in impact if x[0] > fails[-1][0] and x[1] == "200"]
        out["recovery_s"] = r(after[0][0] - t0) if after else None
    else:
        out["first_fail_s"] = out["outage_s"] = out["recovery_s"] = 0.0

    def lat(rows):
        return [x[2] * 1000 for x in rows if x[1] == "200"]
    bl, cl = lat(base), lat(chaos)
    out["baseline_mean_ms"] = r(st.mean(bl), 1) if bl else None
    out["baseline_p95_ms"] = r(p95(bl), 1)
    out["chaos_mean_ms"] = r(st.mean(cl), 1) if cl else None
    out["chaos_p95_ms"] = r(p95(cl), 1)
    out["chaos_max_ms"] = r(max(cl), 1) if cl else None

    pods = [p for p in load_pods(d) if p["name"].startswith("api-")]
    old = before_pods(d)
    after = [p for p in pods if p["t"] >= t0]
    det = [p for p in after if p["ev"] == "DELETED" or "false" in p["ready"]
           or p["phase"] in ("Failed",) or p["reason"] not in ("", "<none>")]
    out["detect_s"] = r(det[0]["t"] - t0) if det else None
    new_ready = [p for p in after if p["name"] not in old and p["ready"] == "true"]
    out["replacement_ready_s"] = r(new_ready[0]["t"] - t0) if new_ready else None
    restarts_seen = {}
    for p in after:
        try:
            restarts_seen[p["name"]] = max(int(x) for x in p["restarts"].split(","))
        except ValueError:
            pass
    base_restarts = {}
    for p in pods:
        if p["t"] < t0:
            try:
                base_restarts[p["name"]] = max(int(x) for x in p["restarts"].split(","))
            except ValueError:
                pass
    out["restarts"] = sum(max(0, v - base_restarts.get(k, 0)) for k, v in restarts_seen.items())
    out["oomkilled"] = "yes" if any("OOMKilled" in p["reason"] for p in after) else "no"
    # max simultaneously ready API pods
    state, best = {}, 0
    for p in pods:
        if p["ev"] == "DELETED":
            state.pop(p["name"], None)
        else:
            state[p["name"]] = p["ready"] == "true"
        best = max(best, sum(state.values()))
    out["max_api_ready"] = best
    return out, m, reqs


def timeline(d, m, reqs, path):
    t0 = m["CHAOS_START"]
    fig, ax = plt.subplots(figsize=(7, 2.6))
    ax.axvspan(0, m["CHAOS_END"] - t0, color=GRID, alpha=0.8, lw=0, label="chaos active")
    ok = [(x[0] - t0, x[2] * 1000) for x in reqs if x[1] == "200"]
    bad = [(x[0] - t0, x[2] * 1000) for x in reqs if x[1] != "200"]
    colour = C1 if m["REPLICAS"] == "1" else C3
    if ok:
        ax.plot(*zip(*ok), ".", ms=3, color=colour, label="successful request")
    if bad:
        top = max([y for _, y in ok] + [1]) * 1.15
        ax.plot([x for x, _ in bad], [top] * len(bad), "x", ms=5, mew=1.2, color=FAIL,
                label=f"failed request ({len(bad)})")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.grid(axis="x", visible=False)
    ax.set_xlabel("seconds since chaos injected")
    ax.set_ylabel("latency (ms, log)")
    ax.set_title(os.path.basename(d), loc="left", fontsize=9, color=INK, pad=18)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=7, frameon=False, ncol=3,
              borderaxespad=0.2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def bar_by_replicas(rows, exp, field, ylabel, path):
    sel = [x for x in rows if x["experiment"] == exp and x[field] is not None]
    if not sel:
        return
    fig, ax = plt.subplots(figsize=(4, 2.8))
    ax.grid(axis="x", visible=False)
    for i, (n, c) in enumerate([(1, C1), (3, C3)]):
        vals = [x[field] for x in sel if x["replicas"] == n]
        if not vals:
            continue
        ax.bar(i, st.mean(vals), width=0.55, color=c, label=f"{n} replica{'s' if n > 1 else ''}")
        ax.plot([i] * len(vals), vals, "o", ms=4, color=INK, mfc="white", zorder=3)
        ax.annotate(f"{st.mean(vals):.1f}", (i, st.mean(vals)), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=8, color=INK)
    ax.set_xticks([0, 1], ["1 replica", "3 replicas"])
    ax.set_ylabel(ylabel)
    ax.set_title(f"{exp}: mean of runs (dots = individual runs)", loc="left", fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def delay_chart(rows, path):
    sel = sorted([x for x in rows if x["experiment"].startswith("delay") and x["replicas"] == 1],
                 key=lambda x: int(x["experiment"][5:-2]))
    if not sel:
        return
    inj = [int(x["experiment"][5:-2]) for x in sel]
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.plot(inj, [x["chaos_mean_ms"] for x in sel], "o-", lw=2, ms=6, color=C1, label="mean latency")
    ax.plot(inj, [x["chaos_p95_ms"] for x in sel], "s--", lw=2, ms=6, color=C3, label="p95 latency")
    ax.plot(inj, [x["baseline_mean_ms"] for x in sel], ":", lw=1.5, color=MUTED, label="baseline mean")
    for x, i in zip(sel, inj):
        ax.annotate(f"{x['error_rate_pct']}% err", (i, x["chaos_p95_ms"]), xytext=(4, 6),
                    textcoords="offset points", fontsize=7, color=MUTED)
    ax.set_xscale("log"); ax.set_yscale("log")
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}")
    ax.set_xticks(inj, [str(i) for i in inj])
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.yaxis.set_major_formatter(fmt)
    ax.set_xlabel("injected delay frontend -> API (ms)")
    ax.set_ylabel("observed latency (ms)")
    ax.set_title("Network delay: latency passes straight through (1 replica)", loc="left", fontsize=9)
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    os.makedirs(FIG, exist_ok=True)
    runs = sorted(d for d in glob.glob(os.path.join(RAW, "*")) if os.path.exists(os.path.join(d, "meta.env")))
    if not runs:
        sys.exit("no runs found in results/raw/")
    rows = []
    for d in runs:
        out, m, reqs = analyse(d)
        rows.append(out)
        timeline(d, m, reqs, os.path.join(FIG, f"timeline-{out['run']}.png"))
    with open(os.path.join(ROOT, "results", "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    bar_by_replicas(rows, "podkill", "outage_s", "user-visible outage (s)", os.path.join(FIG, "podkill-outage.png"))
    bar_by_replicas(rows, "podkill", "failed_requests", "failed requests", os.path.join(FIG, "podkill-failed.png"))
    bar_by_replicas(rows, "podkill", "replacement_ready_s", "new pod Ready after kill (s)",
                    os.path.join(FIG, "podkill-replacement.png"))
    delay_chart(rows, os.path.join(FIG, "delay-latency.png"))
    cols = ["run", "failed_requests", "error_rate_pct", "outage_s", "recovery_s", "detect_s",
            "replacement_ready_s", "restarts", "oomkilled", "max_api_ready", "baseline_mean_ms", "chaos_p95_ms"]
    print(" | ".join(cols))
    for x in rows:
        print(" | ".join(str(x[c]) for c in cols))
    print(f"\nwrote results/summary.csv and {len(os.listdir(FIG))} figures in results/figures/")


if __name__ == "__main__":
    main()
