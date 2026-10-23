from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import time
import click
import schedule

def _job(config_path: str, out_dir: str):
    # 每次运行启动完整 CLI，复用检索/摘要/邮件/站点/去重，且释放上次缓存。
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "arxiv_tracker.cli", "run", "--config", config_path,
         "--out-dir", out_dir, "--verbose"], cwd=root, check=False,
    )
    if result.returncode:
        click.echo(f"[Scheduler] Job failed with exit code {result.returncode}; next run stays scheduled", err=True)
    return result.returncode

@click.command()
@click.option("--time", "time_str", required=True, help="每日触发时间，如 09:00")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, resolve_path=True), required=True, help="配置文件路径（YAML）")
@click.option("--out-dir", type=click.Path(resolve_path=True), default="outputs", help="输出目录")
def main(time_str, config_path, out_dir):
    try:
        schedule.every().day.at(time_str).do(_job, config_path=config_path, out_dir=out_dir)
    except schedule.ScheduleValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--time") from exc
    print(f"[Scheduler] Daily at {time_str} (machine timezone). Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
