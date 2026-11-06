from __future__ import annotations

import json
import pathlib
import re

base = pathlib.Path(__file__).resolve().parents[1] / "app"
source = (base / "responses" / "formatter.py").read_text(encoding="utf-8")
extra = (base / "responses" / "templates.py").read_text(encoding="utf-8")
formatting = (base / "responses" / "formatting.py").read_text(encoding="utf-8")

message_keys = set(re.findall(r'ctx\.message\("([^"]+)"', source))
answer_keys = set(re.findall(r'ctx\.answer\("([^"]+)"', source))
label_keys = set(re.findall(r'ctx\.label\("([^"]+)"', source))
label_keys |= set(re.findall(r'labels\["([^"]+)"\]', formatting))
label_keys |= set(re.findall(r'labels\.get\("([^"]+)"', formatting))
# templates.py dynamic lookups
dynamic = set(re.findall(r'message\("([^"]+)"', extra))
answer_keys |= dynamic

for locale in ("ru", "uz_latn", "uz_cyrl"):
    data = json.loads((base / "language" / f"{locale}.json").read_text(encoding="utf-8"))
    messages = set(data["messages"])
    answers = set(data["answers"])
    labels = set(data["labels"])
    print(f"== {locale}")
    print("missing messages:", sorted(message_keys - messages))
    print("missing answers:", sorted(answer_keys - answers))
    print("missing labels:", sorted(label_keys - labels))
    print("unused answers:", sorted(answers - answer_keys))
