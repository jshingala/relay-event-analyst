"""Score the validator against a hand audit of what the model wrote.

    python -m rea.evaluation.audit eval/narration-x86            # print the table
    python -m rea.evaluation.audit eval/narration-x86 --write    # also add it to RESULTS.md

Inputs, both in the folder:

* ``narration.jsonl``: written by ``rea-eval --narrate``. For each event, every sentence the
  model proposed and the ones the validator rejected.
* ``audit.json``: one hand label per proposed sentence,
  ``{"id": "ev0003", "text": "...", "label": "ok" | "wrong", "note": "why"}``. A sentence is
  "wrong" when it states something the fact sheet contradicts: a number given the wrong meaning,
  a comparison the numbers do not support, a time measured from the wrong reference, a wrong
  label. Vague but true sentences are "ok".

The result is a 2x2 table (hand label x validator decision) plus the sentences where they
disagree.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SECTION = "## Hand audit of the model's sentences"


def load(folder: Path) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    events = [json.loads(line) for line in (folder / "narration.jsonl").read_text().splitlines() if line.strip()]
    labels = {(a["id"], a["text"]): a for a in json.loads((folder / "audit.json").read_text())}
    return events, labels


def score(events: list[dict], labels: dict[tuple[str, str], dict]) -> dict:
    table = {("ok", "kept"): 0, ("ok", "rejected"): 0, ("wrong", "kept"): 0, ("wrong", "rejected"): 0}
    unlabeled: list[tuple[str, str]] = []
    missed: list[dict] = []
    false_rejects: list[dict] = []
    caught: list[dict] = []
    for ev in events:
        if ev.get("model_unavailable"):
            continue
        rejected = {r["text"]: r for r in ev.get("rejected", [])}
        for p in ev.get("proposed", []):
            key = (ev["id"], p["text"])
            lab = labels.get(key)
            if lab is None:
                unlabeled.append(key)
                continue
            decision = "rejected" if p["text"] in rejected else "kept"
            table[(lab["label"], decision)] += 1
            row = {"id": ev["id"], "text": p["text"], "note": lab.get("note", "")}
            if lab["label"] == "wrong" and decision == "kept":
                missed.append(row)
            elif lab["label"] == "ok" and decision == "rejected":
                false_rejects.append(row | {"reasons": rejected[p["text"]]["reasons"]})
            elif lab["label"] == "wrong":
                caught.append(row | {"reasons": rejected[p["text"]]["reasons"]})
    return {"table": table, "unlabeled": unlabeled, "missed": missed, "false_rejects": false_rejects,
            "caught": caught}


def render(res: dict) -> str:
    t = res["table"]
    wrong = t[("wrong", "kept")] + t[("wrong", "rejected")]
    ok = t[("ok", "kept")] + t[("ok", "rejected")]
    lines = [
        SECTION,
        "",
        "Every sentence the model proposed was read against the event's fact sheet and labeled by hand:",
        "*wrong* if the fact sheet contradicts it (a number given the wrong meaning, a comparison the",
        "numbers do not support, a time measured from the wrong reference), *ok* otherwise. Vague but",
        "true counts as ok. Labels: `audit.json`.",
        "",
        "| Hand label | Validator kept | Validator rejected | Total |",
        "|---|---|---|---|",
        f"| ok | {t[('ok', 'kept')]} | {t[('ok', 'rejected')]} | {ok} |",
        f"| wrong | {t[('wrong', 'kept')]} | {t[('wrong', 'rejected')]} | {wrong} |",
        "",
    ]
    if wrong:
        lines.append(f"The validator rejected {t[('wrong', 'rejected')]} of the {wrong} wrong sentences and "
                     f"{t[('ok', 'rejected')]} of the {ok} correct ones.")
    if res["caught"]:
        lines += ["", "Wrong sentences the validator rejected:", ""]
        lines += [f"- `{c['id']}` \"{c['text']}\" Why wrong: {c['note']} Validator: {'; '.join(c['reasons'])}."
                  for c in res["caught"]]
    if res["missed"]:
        lines += ["", "Wrong sentences the validator kept (these reached the report):", ""]
        lines += [f"- `{m['id']}` \"{m['text']}\" Why wrong: {m['note']}" for m in res["missed"]]
    if res["false_rejects"]:
        lines += ["", "Correct sentences the validator rejected:", ""]
        lines += [f"- `{f['id']}` \"{f['text']}\" Validator: {'; '.join(f['reasons'])}. {f['note']}"
                  for f in res["false_rejects"]]
    if res["unlabeled"]:
        lines += ["", f"{len(res['unlabeled'])} sentences have no label yet."]
    return "\n".join(lines) + "\n"


def write_section(results_md: Path, section: str) -> None:
    text = results_md.read_text() if results_md.exists() else ""
    text = re.sub(rf"\n{re.escape(SECTION)}\n.*?(?=\n## |\Z)", "\n", text, flags=re.S).rstrip("\n")
    results_md.write_text(text + "\n\n" + section)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--write", action="store_true", help="add or replace the section in RESULTS.md")
    args = ap.parse_args(argv)
    events, labels = load(args.folder)
    res = score(events, labels)
    section = render(res)
    print(section)
    if args.write:
        write_section(args.folder / "RESULTS.md", section)
    return 1 if res["unlabeled"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
