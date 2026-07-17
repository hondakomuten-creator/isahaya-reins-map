"""
メインエントリポイント — 週次実行スクリプト
1. レインズスクレイピング
2. 国交省取引データ取得
3. ジオコーディング
4. 地図HTML生成
"""

import logging
import sys
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "main.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def main():
    log.info("=" * 60)
    log.info("諫早市 不動産情報マップ更新開始")
    log.info("=" * 60)

    # 1. レインズスクレイピング
    if "--skip-reins" not in sys.argv:
        log.info("【1/4】レインズデータ取得")
        try:
            from reins_scraper import run_scrape
            run_scrape()
        except Exception as e:
            log.error(f"レインズスクレイピングエラー: {e}")
    else:
        log.info("【1/4】レインズスクレイピング スキップ")

    # 1b. たっけんくんスクレイピング
    if "--skip-takken" not in sys.argv:
        log.info("【1b】たっけんくんネットデータ取得")
        try:
            from ntakken_scraper import run_scrape as run_takken
            run_takken()
        except Exception as e:
            log.error(f"たっけんくんスクレイピングエラー: {e}")

    # 2. 国交省取引データ（初回のみ全期間、以降は当年分のみ）
    if "--skip-mlit" not in sys.argv:
        log.info("【2/4】国交省取引データ取得")
        try:
            from mlit_transactions import fetch_transactions
            from datetime import datetime
            # 初回は2019年から、以降は2年分のみ取得
            fetch_transactions(year_from=datetime.now().year - 1)
        except Exception as e:
            log.error(f"国交省APIエラー: {e}")
    else:
        log.info("【2/4】国交省データ スキップ")

    # 3. ジオコーディング
    log.info("【3/4】住所 → 緯度経度変換")
    try:
        from geocoder import geocode_all_properties, geocode_all_transactions
        geocode_all_properties()
        geocode_all_transactions()
    except Exception as e:
        log.error(f"ジオコーディングエラー: {e}")

    # 4. 地図HTML生成
    log.info("【4/4】地図HTML生成")
    try:
        from generate_map import generate_map_html
        output = generate_map_html()
        log.info(f"地図ファイル: {output}")
    except Exception as e:
        log.error(f"地図生成エラー: {e}")

    # 5. GitHubへ自動push（失敗時は3回までリトライ）
    log.info("【5/5】GitHubへ公開")
    import time
    try:
        repo_dir = str(Path(__file__).parent)
        subprocess.run(["git", "add", "output/isahaya_reins_map.html"], cwd=repo_dir, check=True)
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        subprocess.run(["git", "commit", "-m", f"週次更新 {date_str}"], cwd=repo_dir, check=True)
    except subprocess.CalledProcessError as e:
        log.error(f"Git add/commitエラー: {e}")
        return

    for attempt in range(1, 4):
        try:
            # timeout=120: 120秒以内に完了しなければ強制終了してリトライ
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=repo_dir, check=True, timeout=120
            )
            log.info("GitHub Pages 更新完了")
            break
        except subprocess.TimeoutExpired:
            log.warning(f"Git push タイムアウト（{attempt}回目）")
            if attempt < 3:
                log.info("60秒後にリトライします...")
                time.sleep(60)
            else:
                log.error("Git push 3回タイムアウト。次回の自動実行時に再試行されます。")
        except subprocess.CalledProcessError as e:
            log.warning(f"Git push 失敗（{attempt}回目）: {e}")
            if attempt < 3:
                log.info("60秒後にリトライします...")
                time.sleep(60)
            else:
                log.error("Git push 3回失敗。次回の自動実行時に再試行されます。")

    log.info("完了")


if __name__ == "__main__":
    main()
