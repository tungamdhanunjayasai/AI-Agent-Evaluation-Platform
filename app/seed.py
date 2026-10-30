from __future__ import annotations

import json
import os

from app.config import DATABASE_URL, DATASET_PATH
from app.store import Store


def main() -> None:
    store = Store(DATABASE_URL)
    store.init_db()
    tasks = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    version_id = store.upsert_dataset("support", 1, "Support scenarios", tasks)
    configs = [
        ("cfg_correct", "Correct scripted support agent", "correct"),
        ("cfg_false_success", "Convincing false-success agent", "false_success"),
        ("cfg_wrong_order", "Cross-customer access attempt", "wrong_order"),
    ]
    for config_id, name, behavior in configs:
        if not store.get_config(config_id):
            store.create_config(config_id, name, "scripted", {"behavior": behavior})
    if os.getenv("GEMINI_API_KEY") and not store.get_config("cfg_llm_gemini"):
        store.create_config("cfg_llm_gemini", "Gemini LLM support agent", "llm", {"provider": "gemini", "model": "gemini-2.5-flash", "temperature": 0, "limits": {"max_steps": 8}})
    print(f"seeded dataset={version_id} tasks={len(tasks)} configs={len(configs)} database=Supabase PostgreSQL")


if __name__ == "__main__":
    main()

