#!/usr/bin/env python3
"""
Drive Engine — Internal motivation system for a persistent AI agent.

Manages drive states (hunger/satisfaction), selects actions,
and maintains a satisfaction journal.

Usage:
    python3 drive_engine.py status          # Show all drive states
    python3 drive_engine.py hungry          # List drives above threshold
    python3 drive_engine.py pick            # Pick the best action to take
    python3 drive_engine.py satisfy <drive> [--size small|medium|large] [note]
                                            # Record satisfaction (magnitude-weighted)
    python3 drive_engine.py journal [n]     # Show last n journal entries
    python3 drive_engine.py tick            # Advance hunger by elapsed time
    python3 drive_engine.py stats [days]    # Activity stats (default 7 days)
    python3 drive_engine.py streak          # Consecutive-day streaks per drive
    python3 drive_engine.py review [days]   # Self-review: propose parameter changes
                                            # (writes a proposal for human approval;
                                            #  NEVER self-applies)
"""

import json
import os
import sys
import time
import random
from datetime import datetime

import yaml

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(WORKSPACE, "config", "drives.yaml")
STATE_PATH = os.path.join(WORKSPACE, "memory", "drive_state.json")
JOURNAL_PATH = os.path.join(WORKSPACE, "memory", "drive_journal.md")
JOURNAL_JSONL_PATH = os.path.join(WORKSPACE, "memory", "drive_journal.jsonl")
REVIEW_PROPOSAL_PATH = os.path.join(WORKSPACE, "reports", "state", "drive_review_proposal.md")

# Magnitude tiers for satisfy: the numbers finally learn what the prose knows.
# small  = routine/low-effort action (tidying, a quick check that found nothing new)
# medium = a normal solid action (default; matches v1.0 behavior)
# large  = high-impact/high-effort (caught a silent outage, shipped something real)
SIZE_MULTIPLIERS = {"small": 0.5, "medium": 1.0, "large": 1.5}


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state["_last_tick"] = time.time()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def init_drive(name, config):
    """Initialize a drive's state if it doesn't exist."""
    return {
        "hunger": 0.0,
        "satisfaction": 0.0,
        "last_action": None,
        "action_count": 0,
        "total_satisfaction": 0.0,
    }


def tick_drives(state, config):
    """Advance all drives by elapsed time since last tick."""
    now = time.time()
    last_tick = state.get("_last_tick", now)
    elapsed_hours = (now - last_tick) / 3600.0

    if elapsed_hours <= 0:
        return state

    drives_config = config.get("drives", {})

    for name, dcfg in drives_config.items():
        ds = state.get(name, init_drive(name, dcfg))

        # Hunger rises
        hunger_rate = dcfg.get("hunger_rate", 2.0)
        ds["hunger"] = min(100.0, ds["hunger"] + hunger_rate * elapsed_hours)

        # Satisfaction decays
        decay_rate = dcfg.get("decay_rate", 1.0)
        ds["satisfaction"] = max(0.0, ds["satisfaction"] - decay_rate * elapsed_hours)

        state[name] = ds

    return state


def get_hungry_drives(state, config):
    """Return drives whose hunger exceeds their threshold."""
    hungry = []
    drives_config = config.get("drives", {})

    for name, dcfg in drives_config.items():
        ds = state.get(name, {})
        hunger = ds.get("hunger", 0)
        threshold = dcfg.get("threshold", 50)
        cooldown = dcfg.get("cooldown_minutes", 30)
        last_action = ds.get("last_action")

        # Check cooldown
        if last_action and (time.time() - last_action) < cooldown * 60:
            continue

        if hunger >= threshold:
            hungry.append({
                "name": name,
                "hunger": hunger,
                "threshold": threshold,
                "satiation": dcfg.get("satiation", 50),
                "description": dcfg.get("description", ""),
                "actions": dcfg.get("actions", []),
                "satisfaction_log": dcfg.get("satisfaction_log", ""),
            })

    return hungry


def pick_action(state, config):
    """Select the best drive to act on and suggest an action."""
    hungry = get_hungry_drives(state, config)
    if not hungry:
        return None

    strategy = config.get("system", {}).get("selection_strategy", "weighted")

    if strategy == "hungriest":
        hungry.sort(key=lambda d: d["hunger"], reverse=True)
    elif strategy == "most_satisfying":
        hungry.sort(key=lambda d: d["satiation"], reverse=True)
    else:  # weighted
        hungry.sort(key=lambda d: d["hunger"] * d["satiation"], reverse=True)

    drive = hungry[0]
    action = random.choice(drive["actions"]) if drive["actions"] else "Follow the drive"

    return {
        "drive": drive["name"],
        "hunger": drive["hunger"],
        "description": drive["description"],
        "suggested_action": action,
        "satisfaction_log": drive["satisfaction_log"],
        "satiation_reward": drive["satiation"],
    }


def satisfy_drive(state, config, drive_name, note="", size="medium"):
    """Record satisfaction for a drive, weighted by action magnitude."""
    drives_config = config.get("drives", {})
    dcfg = drives_config.get(drive_name)
    if not dcfg:
        print(f"Unknown drive: {drive_name}")
        sys.exit(1)

    ds = state.get(drive_name, init_drive(drive_name, dcfg))

    mult = SIZE_MULTIPLIERS.get(size, 1.0)
    satiation = dcfg.get("satiation", 50) * mult
    ds["hunger"] = max(0.0, ds["hunger"] - satiation)
    ds["satisfaction"] = min(100.0, ds["satisfaction"] + satiation)
    ds["last_action"] = time.time()
    ds["action_count"] = ds.get("action_count", 0) + 1
    ds["total_satisfaction"] = ds.get("total_satisfaction", 0) + satiation

    state[drive_name] = ds

    # Write to journal (markdown for humans, JSONL mirror for tools)
    journal_entry = format_journal_entry(drive_name, dcfg, note, size=size)
    append_journal(journal_entry)
    append_journal_jsonl({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "drive": drive_name,
        "reward": round(satiation, 1),
        "size": size,
        "hunger_after": round(ds["hunger"], 2),
        "satisfaction_after": round(ds["satisfaction"], 2),
        "action_count": ds["action_count"],
        "note": note,
    })

    return state, satiation


def format_journal_entry(drive_name, dcfg, note="", size="medium"):
    """Format a satisfaction journal entry."""
    now = datetime.now()
    satisfaction_log = dcfg.get("satisfaction_log", "")
    title = f"### {now.strftime('%Y-%m-%d %H:%M')} — {drive_name.title()}"
    if size != "medium":
        title += f" ({size})"
    lines = [
        title,
        f"*{satisfaction_log}*",
    ]
    if note:
        lines.append(f"**What happened:** {note}")
    lines.append("")
    return "\n".join(lines)


def append_journal(entry):
    """Append to the satisfaction journal."""
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)

    # Create header if new file
    if not os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "w") as f:
            f.write("# Drive Satisfaction Journal\n")
            f.write("*A record of actions taken from internal motivation.*\n\n")

    with open(JOURNAL_PATH, "a") as f:
        f.write(entry + "\n")


def append_journal_jsonl(record):
    """Append a structured satisfaction event to the JSONL mirror.

    The markdown journal is for humans; this is for mlr/duckdb/jq queries.
    Failures here never block the satisfy action.
    """
    try:
        os.makedirs(os.path.dirname(JOURNAL_JSONL_PATH), exist_ok=True)
        with open(JOURNAL_JSONL_PATH, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cmd_status(state, config):
    """Display all drive states."""
    drives_config = config.get("drives", {})

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║                    DRIVE STATUS                             ║")
    print("╠══════════════════════════════════════════════════════════════╣")

    for name, dcfg in drives_config.items():
        ds = state.get(name, {})
        hunger = ds.get("hunger", 0)
        satisfaction = ds.get("satisfaction", 0)
        threshold = dcfg.get("threshold", 50)
        count = ds.get("action_count", 0)
        total_sat = ds.get("total_satisfaction", 0)

        # Visual hunger bar
        bar_len = 30
        filled = int(hunger / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        active = "🔥" if hunger >= threshold else "  "
        sat_indicator = "✨" if satisfaction > 50 else "  "

        print(f"║ {active} {name:14s} [{bar}] {hunger:5.1f}/{threshold}")
        print(f"║ {sat_indicator} satisfaction: {satisfaction:5.1f}  │  actions: {count}  │  lifetime: {total_sat:.0f}")
        print(f"║    {dcfg.get('description', '')[:58]}")
        print("╠══════════════════════════════════════════════════════════════╣")

    print("╚══════════════════════════════════════════════════════════════╝")


def cmd_hungry(state, config):
    """List drives above threshold."""
    hungry = get_hungry_drives(state, config)
    if not hungry:
        print("All drives satisfied. Nothing pressing.")
        return

    for d in hungry:
        print(f"🔥 {d['name']:14s} hunger={d['hunger']:.1f} (threshold {d['threshold']})")
        print(f"   {d['description']}")


def cmd_pick(state, config):
    """Pick the best action."""
    choice = pick_action(state, config)
    if not choice:
        print("No drives are hungry enough to act on.")
        print("STATUS: content")
        return

    print(f"DRIVE: {choice['drive']}")
    print(f"HUNGER: {choice['hunger']:.1f}")
    print(f"ACTION: {choice['suggested_action']}")
    print(f"REWARD: {choice['satiation_reward']} satisfaction points")
    print(f"ON_COMPLETION: {choice['satisfaction_log']}")


def cmd_journal(n=10):
    """Show recent journal entries."""
    if not os.path.exists(JOURNAL_PATH):
        print("No journal entries yet.")
        return

    with open(JOURNAL_PATH) as f:
        lines = f.readlines()

    # Find last n entries (each starts with ###)
    entries = []
    current = []
    for line in lines:
        if line.startswith("### "):
            if current:
                entries.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append("".join(current))

    for entry in entries[-n:]:
        print(entry)


def cmd_stats(state, config, days=7):
    """Show drive activity over the last N days (velocity + neglect)."""
    drives_config = config.get("drives", {})
    if not os.path.exists(JOURNAL_PATH):
        print("No journal yet.")
        return

    cutoff = datetime.now().timestamp() - days * 86400
    counts = {name: 0 for name in drives_config}
    last_seen = {name: None for name in drives_config}

    with open(JOURNAL_PATH) as f:
        for line in f:
            # Header lines look like: "### 2026-05-12 08:00 \u2014 Stewardship"
            if not line.startswith("### "):
                continue
            try:
                stamp = line.split(" \u2014 ")[0][4:].strip()
                drive = line.split(" \u2014 ")[1].strip().lower()
            except IndexError:
                continue
            if drive not in counts:
                continue
            try:
                ts = datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                continue
            if ts >= cutoff:
                counts[drive] += 1
            if last_seen[drive] is None or ts > last_seen[drive]:
                last_seen[drive] = ts

    now_ts = datetime.now().timestamp()
    print(f"\n\U0001f4ca Drive activity (last {days} days)")
    print("=" * 56)
    for name in drives_config:
        last = last_seen[name]
        if last is None:
            ago = "never"
        else:
            hours = (now_ts - last) / 3600.0
            ago = f"{hours:.1f}h ago" if hours < 48 else f"{hours/24:.1f}d ago"
        flag = " \u26a0\ufe0f neglected" if counts[name] == 0 else ""
        print(f"  {name:14s} {counts[name]:>3d} acts in {days}d  │  last: {ago}{flag}")
    total = sum(counts.values())
    print("-" * 56)
    print(f"  total: {total} actions  │  velocity: {total/days:.1f}/day")
    print()


def cmd_streak(config):
    """Show consecutive-day satisfaction streaks per drive (from JSONL journal)."""
    drives_config = config.get("drives", {})
    if not os.path.exists(JOURNAL_JSONL_PATH):
        print("No JSONL journal yet.")
        return

    from collections import defaultdict
    days_by_drive = defaultdict(set)
    with open(JOURNAL_JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                drive = rec.get("drive")
                ts = rec.get("ts", "")
                if drive and len(ts) >= 10:
                    days_by_drive[drive].add(ts[:10])
            except json.JSONDecodeError:
                continue

    from datetime import date, timedelta
    today = date.today()

    def streak_lengths(day_set):
        if not day_set:
            return 0, 0
        # current streak: count back from today (or yesterday) consecutively
        current = 0
        cursor = today
        # allow today missing — start from yesterday if today not present
        if today.isoformat() not in day_set:
            cursor = today - timedelta(days=1)
        while cursor.isoformat() in day_set:
            current += 1
            cursor -= timedelta(days=1)
        # best streak across all days
        sorted_days = sorted(date.fromisoformat(d) for d in day_set)
        best = run = 1
        for i in range(1, len(sorted_days)):
            if (sorted_days[i] - sorted_days[i-1]).days == 1:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return current, best

    print("\n\U0001f525 Drive streaks (consecutive days with at least one action)")
    print("=" * 60)
    for name in drives_config:
        cur, best = streak_lengths(days_by_drive.get(name, set()))
        total_days = len(days_by_drive.get(name, set()))
        flame = " \U0001f525" if cur >= 3 else ""
        print(f"  {name:14s} current: {cur:>3d}d  │  best: {best:>3d}d  │  active days: {total_days}{flame}")
    print()


def cmd_tick(state, config, quiet=False):
    """Advance hunger by elapsed time and show result."""
    state = tick_drives(state, config)
    save_state(state)
    if quiet:
        # One line per drive: name hunger/threshold (satisfaction)
        parts = []
        for name, dcfg in config["drives"].items():
            d = state.get(name, {}) or {}
            hunger = d.get("hunger", 0.0)
            threshold = dcfg.get("threshold", 50)
            fire = "\U0001f525" if hunger >= threshold else ""
            parts.append(f"{name}={hunger:.0f}/{threshold}{fire}")
        print("drives: " + " ".join(parts))
        return
    print("Drives ticked.")
    cmd_status(state, config)


def cmd_satisfy(state, config, drive_name, note="", size="medium"):
    """Record satisfaction."""
    state, reward = satisfy_drive(state, config, drive_name, note, size=size)
    save_state(state)
    drives_config = config.get("drives", {})
    dcfg = drives_config.get(drive_name, {})
    tag = "" if size == "medium" else f" [{size}]"
    print(f"✨ {dcfg.get('satisfaction_log', 'Satisfied.')}{tag}")
    print(f"   +{reward:.0f} satisfaction  │  hunger now: {state[drive_name]['hunger']:.1f}")


def cmd_review(state, config, days=90):
    """Quarterly self-review: analyze the journal and PROPOSE parameter changes.

    Writes a human-readable proposal to reports/state/drive_review_proposal.md.
    Deliberately has no --apply flag: parameter changes go through the human.
    Self-modification with a keeper.
    """
    drives_config = config.get("drives", {})
    if not os.path.exists(JOURNAL_JSONL_PATH):
        print("No JSONL journal yet — nothing to review.")
        return

    cutoff = datetime.now().timestamp() - days * 86400
    counts = {name: 0 for name in drives_config}
    sizes = {name: {"small": 0, "medium": 0, "large": 0} for name in drives_config}
    last_seen = {name: None for name in drives_config}

    with open(JOURNAL_JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            drive = rec.get("drive")
            if drive not in counts:
                continue
            try:
                ts = datetime.fromisoformat(rec.get("ts", "")).timestamp()
            except ValueError:
                continue
            if last_seen[drive] is None or ts > last_seen[drive]:
                last_seen[drive] = ts
            if ts >= cutoff:
                counts[drive] += 1
                sz = rec.get("size", "medium")
                if sz in sizes[drive]:
                    sizes[drive][sz] += 1

    total = sum(counts.values())
    if total == 0:
        print(f"No journal activity in the last {days} days — nothing to review.")
        return

    now_ts = datetime.now().timestamp()
    proposals = []
    observations = []

    for name, dcfg in drives_config.items():
        share = counts[name] / total
        hunger_rate = dcfg.get("hunger_rate", 2.0)
        last = last_seen[name]
        idle_days = (now_ts - last) / 86400 if last else None

        obs = f"- **{name}**: {counts[name]} actions ({share:.0%} share), hunger_rate {hunger_rate}"
        if idle_days is not None:
            obs += f", last acted {idle_days:.1f}d ago"
        observations.append(obs)

        # Heuristic 1: monopoly — one drive eating >40% of all actions
        if share > 0.40:
            proposals.append(
                f"**{name}** holds {share:.0%} of all actions (monopoly threshold 40%). "
                f"Consider raising its threshold (currently {dcfg.get('threshold', 50)}) "
                f"by ~10% so cheaper satisfies don't crowd out harder drives."
            )

        # Heuristic 2: starvation — <8% share suggests hunger_rate too slow to compete
        if share < 0.08 and counts[name] > 0:
            new_rate = round(hunger_rate * 1.25, 2)
            proposals.append(
                f"**{name}** has only {share:.0%} share. Propose hunger_rate "
                f"{hunger_rate} → {new_rate} (+25%) so it wins the pick more often."
            )

        # Heuristic 3: full neglect in window
        if counts[name] == 0:
            proposals.append(
                f"**{name}** took ZERO actions in {days} days. Either raise its "
                f"hunger_rate meaningfully or discuss whether this drive still fits."
            )

        # Heuristic 4: size inflation — if >60% of sized actions are 'large',
        # grading has drifted; large should be rare.
        sized = sizes[name]["small"] + sizes[name]["large"] + sizes[name]["medium"]
        if sized >= 10 and sizes[name]["large"] / sized > 0.60:
            proposals.append(
                f"**{name}**: {sizes[name]['large']}/{sized} actions graded 'large'. "
                f"Grade inflation — recalibrate what counts as high-impact."
            )

    # Build the proposal document
    lines = [
        "# Drive System Self-Review",
        f"*Window: last {days} days • generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "This is a PROPOSAL. Nothing here is applied automatically — parameter",
        "changes to `config/drives.yaml` require human approval. Self-modification",
        "with a keeper.",
        "",
        "## Observations",
        *observations,
        "",
        "## Proposed changes",
    ]
    if proposals:
        lines += [f"{i+1}. {p}" for i, p in enumerate(proposals)]
    else:
        lines.append("No changes proposed — the current parameters look balanced.")
    lines += [
        "",
        "## To apply",
        "Review with your human, edit `config/drives.yaml` together, and note the",
        "decision in the daily memory file. The engine never edits its own config.",
        "",
    ]

    os.makedirs(os.path.dirname(REVIEW_PROPOSAL_PATH), exist_ok=True)
    with open(REVIEW_PROPOSAL_PATH, "w") as f:
        f.write("\n".join(lines))

    print("\n".join(lines))
    print(f"[proposal written to {REVIEW_PROPOSAL_PATH}]")


if __name__ == "__main__":
    config = load_config()
    state = load_state()

    # Always tick first
    state = tick_drives(state, config)
    save_state(state)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        cmd_status(state, config)
    elif cmd == "hungry":
        cmd_hungry(state, config)
    elif cmd == "pick":
        cmd_pick(state, config)
    elif cmd == "tick":
        cmd_tick(state, config, quiet=("--quiet" in sys.argv or "-q" in sys.argv))
    elif cmd == "satisfy":
        if len(sys.argv) < 3:
            print("Usage: drive_engine.py satisfy <drive> [--size small|medium|large] [note]")
            sys.exit(1)
        rest = sys.argv[3:]
        size = "medium"
        if "--size" in rest:
            i = rest.index("--size")
            if i + 1 < len(rest) and rest[i + 1] in SIZE_MULTIPLIERS:
                size = rest[i + 1]
                rest = rest[:i] + rest[i + 2:]
            else:
                print(f"--size must be one of: {', '.join(SIZE_MULTIPLIERS)}")
                sys.exit(1)
        note = " ".join(rest)
        cmd_satisfy(state, config, sys.argv[2], note, size=size)
    elif cmd == "journal":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        cmd_journal(n)
    elif cmd == "stats":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        cmd_stats(state, config, days)
    elif cmd == "streak":
        cmd_streak(config)
    elif cmd == "review":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        cmd_review(state, config, days)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
