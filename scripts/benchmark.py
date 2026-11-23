"""Run tests.json against a deployed (or local) Agent API and print a markdown benchmark table.

    python scripts/benchmark.py https://your-space.hf.space
"""
import json
import re
import sys
import time
import uuid
from pathlib import Path

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:7860").rstrip("/")
RUN = uuid.uuid4().hex[:6]
cases = json.loads((Path(__file__).resolve().parent.parent / "tests.json").read_text())


def fill(s: str) -> str:
    return s.replace("{run}", RUN)


def check(case, responses) -> list[str]:
    c, last = case["checks"], responses[-1]
    names = [t["name"] for t in last["tool_calls"]]
    errors = []
    for t in c.get("tools_include", []):
        if t not in names:
            errors.append(f"missing tool {t}")
    for t in c.get("tools_exclude", []):
        if t in names:
            errors.append(f"unexpected tool {t}")
    if "tools_exact" in c and names != c["tools_exact"]:
        errors.append(f"tools {names} != {c['tools_exact']}")
    for t in c.get("first_step_tools_include", []):
        if t not in [x["name"] for x in responses[0]["tool_calls"]]:
            errors.append(f"first step missing tool {t}")
    for p in c.get("reply_matches", []):
        if not re.search(fill(p), last["reply"]):
            errors.append(f"reply lacks /{p}/")
    for p in c.get("reply_not_matches", []):
        if re.search(fill(p), last["reply"]):
            errors.append(f"reply has /{p}/")
    if "max_tokens_out" in c and last["tokens"]["out"] > c["max_tokens_out"]:
        errors.append(f"tokens out {last['tokens']['out']} > {c['max_tokens_out']}")
    return errors


assert requests.get(BASE + "/health", timeout=30).text.strip().strip('"') == "ok", "health check failed"
rows, total_in, total_out, passed = [], 0, 0, 0
for case in cases:
    responses, latency = [], 0.0
    for step in case["steps"]:
        body = {k: fill(v) for k, v in step.items()}
        t0 = time.time()
        r = requests.post(BASE + "/chat", json=body, timeout=180)
        latency = time.time() - t0
        data = r.json()
        responses.append(data)
        total_in += data["tokens"]["in"]
        total_out += data["tokens"]["out"]
    errors = check(case, responses)
    passed += not errors
    rows.append((case["id"], case["category"], case["name"], "PASS" if not errors else "FAIL",
                 f"{latency:.1f}s", responses[-1]["tokens"]["in"], responses[-1]["tokens"]["out"],
                 ", ".join(t["name"] for t in responses[-1]["tool_calls"]) or "-", "; ".join(errors)))
    print(f"[{rows[-1][3]}] {case['id']}. {case['name']} {errors or ''}", file=sys.stderr)

print("| # | Category | Test | Result | Latency (last step) | Tokens in | Tokens out | Tools called | Notes |")
print("|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x) for x in r) + " |")
print(f"\n**{passed}/{len(cases)} passed** · total tokens {total_in} in / {total_out} out")
Path("benchmark_results.json").write_text(json.dumps({"run": RUN, "rows": rows}, indent=2))
