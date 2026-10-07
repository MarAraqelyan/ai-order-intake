"""
Check the format-1 (legacy) real-call recordings against the evidence that exists.

Format-1 recordings (made on 2026-10-07 before the fingerprint change) stored:
  recording_key = sha256(request_id + sorted JSON of [system, user] messages + model)
They did NOT store the catalog, tool definition, output schema, finish_reason,
response id, or the model id returned by the API.

This script re-derives what can be verified:
  1. The stored key is reproduced from the saved system-prompt snapshot, the
     request text file, and the model name -> request text, prompt, and requested
     model are verified.
  2. Every resolved SKU in the final answer has a search_catalog call in the same
     recording that returned found=true for that SKU.
  3. Catalog values returned by the tool match the current data/catalog.json
     (consistent with, but not proof of, the catalog used at the time).

Usage:  python scripts/verify_legacy_recordings.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY_DIR = ROOT / "recordings" / "legacy-v1"
PROMPT_SNAPSHOT = ROOT / "ai-workflow" / "prompt-snapshots" / "system-prompt-v1.txt"
MANIFEST = ROOT / "data" / "manifest.json"
CATALOG = ROOT / "data" / "catalog.json"


def v1_key(request_id: str, system_prompt: str, text: str, model: str) -> str:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
    sorted_msgs = sorted(json.dumps(m, sort_keys=True) for m in messages)
    return hashlib.sha256((request_id + "".join(sorted_msgs) + model).encode("utf-8")).hexdigest()


def main() -> int:
    prompt = PROMPT_SNAPSHOT.read_text(encoding="utf-8")
    manifest = {e["request_id"]: e for e in json.loads(MANIFEST.read_text())["requests"]}
    catalog = {p["sku"]: p for p in json.loads(CATALOG.read_text())}
    problems = 0
    files = sorted(LEGACY_DIR.glob("R*.json"), key=lambda p: int(p.name[1:].split("_")[0]))
    for path in files:
        rec = json.loads(path.read_text(encoding="utf-8"))
        rid = rec["request_id"]
        text = (ROOT / "data" / "requests" / manifest[rid]["file"]).read_text(encoding="utf-8").strip()
        key_ok = v1_key(rid, prompt, text, rec["model"]) == rec["recording_key"]

        final = json.loads(rec["final_response"])
        found = {tc["result"]["sku"] for tc in rec["tool_calls"] if tc["result"].get("found")}
        evidence_ok = all(line["sku"] in found
                          for line in final["lines"] if line["sku"])
        catalog_ok = all(tc["result"]["unit_cents"] == catalog[tc["result"]["sku"]]["unit_cents"]
                         for tc in rec["tool_calls"] if tc["result"].get("found"))
        queries = [tc["arguments"]["query"] for tc in rec["tool_calls"]]
        ok = key_ok and evidence_ok and catalog_ok
        problems += not ok
        print(f"{path.name:22} key(text+prompt+model)={'OK' if key_ok else 'MISMATCH'}  "
              f"sku-evidence={'OK' if evidence_ok else 'MISSING'}  "
              f"tool-prices={'OK' if catalog_ok else 'DIFFER'}  "
              f"model={rec['model']}  searches={queries}  "
              f"lines={[(l['sku'], l['quantity'], l['is_unresolved']) for l in final['lines']]}")
    print(f"\n{len(files)} legacy recordings checked, {problems} with problems.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
