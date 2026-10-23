"""Manual Qwen connection check; importing this module never makes a request."""
import os
from pathlib import Path

import yaml

from arxiv_tracker.llm import _chat_completions_request


def main():
    config = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml").read_text(encoding="utf-8"))
    llm = config["llm"]
    key_name = llm.get("api_key_env", "DASHSCOPE_API_KEY")
    key = os.getenv(key_name, "")
    if not key:
        print(f"Missing {key_name}; set the environment variable before running this check.")
        return 1
    reply = _chat_completions_request(
        base_url=llm["base_url"], model=llm["model"], api_key=key,
        messages=[{"role": "user", "content": "Say hello in one short sentence."}],
        max_tokens=64, disable_thinking=True,
    )
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
