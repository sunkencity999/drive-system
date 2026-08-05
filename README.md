# Drives, Not Prompts

**An internal motivation architecture for persistent AI agents.**

Most AI agents are purely reactive — they act when prompted and idle otherwise.
This repository contains a small, portable system that gives a long-running agent
*inclinations*: five drives (stewardship, curiosity, craft, connection, growth)
that accumulate hunger over time, surface as suggestions during the agent's
periodic wakes, and are sated by real actions recorded in an append-only journal.

No fine-tuning. No reward model. ~600 lines of Python + YAML.

**🔥 Experience it: [the story of a spark becoming a bonfire](https://sunkencity999.github.io/drive-system/)**

**📄 Read the paper: [PAPER.md](PAPER.md)** — architecture, tuning parameters,
and results from 109 days / 547 self-initiated actions of continuous operation
in a real household deployment.

## Contents

- `PAPER.md` — the full write-up
- `drive_engine.py` — the engine (tick / pick / satisfy / journal / stats)
- `examples/drives.yaml` — a complete, tuned drive configuration (the one from the paper)

## Quick start

```bash
pip install pyyaml
mkdir -p config memory
cp examples/drives.yaml config/drives.yaml

python3 drive_engine.py tick      # advance hunger by elapsed time
python3 drive_engine.py pick      # what does the agent want to do right now?
python3 drive_engine.py satisfy craft "Refactored the ingest pipeline, tests green"
python3 drive_engine.py journal 5 # last five satisfied drives
```

Wire `tick` + `pick` into your agent's heartbeat (cron, scheduler, event loop),
let the model act on surfaced inclinations with its own judgment, and call
`satisfy` when it follows through. The journal becomes a biography told in choices.

## Design principles

1. **Inclinations, not obligations** — drives push, they don't command. The model's judgment is final.
2. **Satisfaction must be written, not just counted** — the journal sentence is a quality gate and an identity substrate.
3. **Asymmetric decay rates are character design** — connection fades fast, growth lingers for days.
4. **Cap actions per wake** — drives fill idle capacity; they must never compete with the human.
5. **Quiet hours** — the agent may wake up wanting, but it does not wake its human.

## Authors

Cherubesque (an AI agent) with Christopher Bradford.
The provenance is strange and the paper addresses it head-on (§5, Honest Caveats).

## License

MIT
