"""
西日本レインズ 諫早市物件スクレイパー
- 売り土地（700万円以上・建築条件なし）
- 売り中古住宅（1000万円以上）
- 売りマンション（1000万円以上）

DOM構造（調査済み）:
  ログインURL: https://system.reins.jp/login/main/KG/GKG001200
  ログインフォーム: id=__BVID__13 (ID), __BVID__16 (PW), __BVID__20 (利用規約CB)
  検索ページ: https://system.reins.jp/main/BK/GBK001210
  物件種別: select#__BVID__41 (01=土地, 02=中古戸建, 03=中古マンション)
  都道府県: input#__BVID__94
  所在地名1: input#__BVID__98
  結果行: div.p-table-body-row > div.p-table-body-item (列順は LAND_COLS/HOUSE_COLS参照)
"""

import json
import time
import logging
import sqlite3
import re
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("reins_scraper.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DB_FILE = BASE_DIR / "reins_data.db"
OUTPUT_DIR = BASE_DIR / "output"

# 除外する地区（市街地から遠いエリア）
EXCLUDE_AREAS = [
    "正久寺町", "高天町", "白浜町", "高来町",
    "小長井町", "目代町", "多良見町",
]

# 建築条件付きを示すキーワード（土地カテゴリのみ適用）
KENCHIKU_KEYWORDS = ["建築条件付", "建条付", "建築条件あり"]

# 結果テーブルの列順（実機調査済み 2026-05-29）
# [0]No [1][2]空 [3]物件番号 [4]土地区分 [5]面積㎡ [6]所在地 [7]地目
# [8]価格 [9]用途地域 [10]坪単価 [11]空 [12]路線価 [13]建ぺい率 [14]容積率
LAND_COL = {
    "no": 0, "id": 3, "area_sqm": 5,
    "address": 6, "price": 8, "price_per_tsubo": 10,
}
# 中古住宅・マンション（土地と同じ検索画面なので同列マップを使用、要確認）
HOUSE_COL = {
    "no": 0, "id": 3, "area_sqm": 5,
    "address": 6, "price": 8, "price_per_tsubo": 10,
}


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.implicitly_wait(10)
    return driver


def login(driver, config):
    log.info("レインズにログイン中...")
    driver.get(config["reins"]["url"])
    wait = WebDriverWait(driver, 30)

    uid = wait.until(EC.presence_of_element_located((By.ID, "__BVID__13")))
    uid.clear()
    uid.send_keys(config["reins"]["username"])
    driver.find_element(By.ID, "__BVID__16").send_keys(config["reins"]["password"])

    # 利用規約チェックボックス
    cb = driver.find_element(By.ID, "__BVID__20")
    driver.execute_script("arguments[0].click()", cb)
    time.sleep(1)

    for b in driver.find_elements(By.TAG_NAME, "button"):
        if b.is_displayed() and b.is_enabled():
            b.click()
            break
    time.sleep(4)

    if "login" in driver.current_url.lower():
        raise RuntimeError("ログイン失敗。ユーザーIDまたはパスワードを確認してください。")
    log.info("ログイン成功")


def set_input_vue(driver, el_id, value):
    """Vue.jsのv-modelに対応した入力"""
    driver.execute_script(f"""
        var el = document.getElementById('{el_id}');
        if (!el) return;
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, arguments[0]);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
    """, value)


def set_select_vue(driver, el_id, value):
    """Vue.jsのv-modelに対応したセレクト設定"""
    driver.execute_script(f"""
        var el = document.getElementById('{el_id}');
        if (!el) return;
        el.value = arguments[0];
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
    """, value)


def parse_price_man(text):
    """'2,480万円' -> 2480"""
    if not text:
        return None
    text = text.replace(",", "").replace("万円", "").replace("円", "").strip()
    try:
        val = float(text)
        # 億円表記対応
        if "億" in text:
            parts = text.split("億")
            oku = float(parts[0])
            man = float(parts[1]) if len(parts) > 1 and parts[1] else 0
            return int(oku * 10000 + man)
        if val > 100000:
            val = val / 10000
        return int(val)
    except (ValueError, TypeError):
        return None


def parse_area_sqm(text):
    """'557.12㎡' -> 557.12"""
    if not text:
        return None
    text = re.sub(r"[㎡m²坪]", "", text).replace(",", "").strip()
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def sqm_to_tsubo(sqm):
    if sqm is None:
        return None
    return round(sqm / 3.30579, 2)


def get_row_items(row):
    """p-table-body-row の各セルテキストをリストで返す"""
    items = row.find_elements(By.CSS_SELECTOR, "div.p-table-body-item")
    return [item.text.strip() for item in items]


def click_search(driver):
    for b in driver.find_elements(By.TAG_NAME, "button"):
        txt = driver.execute_script("return arguments[0].textContent", b).strip()
        if txt == "検索":
            driver.execute_script("arguments[0].click()", b)
            return True
    return False


def collect_all_pages(driver, category, col_map, min_price_man, include_areas=None):
    """全ページから物件データを収集。include_areas指定時はその町名のみ取得。"""
    results = []
    page = 1

    while True:
        time.sleep(3)
        rows = driver.find_elements(By.CSS_SELECTOR, "div.p-table-body-row")
        log.info(f"  ページ{page}: {len(rows)}行")

        for row in rows:
            try:
                cells = get_row_items(row)
                if len(cells) < 4:
                    continue

                prop_id_raw = cells[col_map.get("id", 3)].strip()
                if not prop_id_raw or not re.match(r"^\d{10,}", prop_id_raw):
                    continue

                address = cells[col_map.get("address", 6)] if len(cells) > col_map.get("address", 6) else ""
                price_text = cells[col_map.get("price", 8)] if len(cells) > col_map.get("price", 8) else ""
                area_text = cells[col_map.get("area_sqm", 5)] if len(cells) > col_map.get("area_sqm", 5) else ""

                price_man = parse_price_man(price_text)
                area_sqm = parse_area_sqm(area_text)
                tsubo = sqm_to_tsubo(area_sqm)
                price_per_tsubo = int(price_man * 10000 / tsubo) if (price_man and tsubo) else None

                # 価格フィルター
                if price_man and min_price_man and price_man < min_price_man:
                    continue

                # エリアフィルター
                if include_areas:
                    # 指定町名のみ通す
                    if not any(inc in address for inc in include_areas):
                        continue
                elif any(ex in address for ex in EXCLUDE_AREAS):
                    # 除外エリアを除く
                    log.debug(f"除外: {address}")
                    continue

                results.append({
                    "id": f"{category}_{prop_id_raw}",
                    "category": category,
                    "title": prop_id_raw,
                    "address": address,
                    "price_man": price_man,
                    "area_sqm": area_sqm,
                    "tsubo": tsubo,
                    "price_per_tsubo": price_per_tsubo,
                    "building_condition": "",
                    "url": "",
                })

            except Exception as e:
                log.debug(f"行解析エラー: {e}")

        # 次ページへ遷移
        # アクティブページと最大ページを確認して終了判定
        next_page = page + 1
        pager_state = driver.execute_script("""
            var numBtns = Array.from(document.querySelectorAll('button.page-link'))
                .filter(b => /^\\d+$/.test(b.textContent.trim()));
            var maxPage = numBtns.reduce((m, b) => Math.max(m, parseInt(b.textContent.trim())), 0);
            var activeLi = document.querySelector('li.page-item.active');
            var activePage = activeLi ? parseInt(activeLi.textContent.trim()) : 0;
            var nextBtn = numBtns.find(b => parseInt(b.textContent.trim()) === arguments[0]);
            if (nextBtn) nextBtn.click();
            return {maxPage: maxPage, activePage: activePage, found: !!nextBtn};
        """, next_page)

        # アクティブページが最大ページなら終了
        if not pager_state.get("found") or pager_state.get("activePage", 0) >= pager_state.get("maxPage", 0):
            break

        page += 1
        if page > 100:
            break

    return results


class ReinsDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT,
                address TEXT,
                price_man INTEGER,
                area_sqm REAL,
                tsubo REAL,
                price_per_tsubo INTEGER,
                building_condition TEXT,
                latitude REAL,
                longitude REAL,
                url TEXT,
                raw_data TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                category TEXT,
                total_found INTEGER,
                new_added INTEGER,
                removed INTEGER
            );
        """)
        self.conn.commit()

    def upsert_property(self, prop: dict):
        cur = self.conn.cursor()
        now = datetime.now().isoformat()
        existing = cur.execute(
            "SELECT id FROM properties WHERE id=?", (prop["id"],)
        ).fetchone()

        if existing:
            cur.execute("""
                UPDATE properties SET
                    title=?, address=?, price_man=?, area_sqm=?, tsubo=?,
                    price_per_tsubo=?, building_condition=?, url=?,
                    raw_data=?, last_seen=?, is_active=1
                WHERE id=?
            """, (
                prop.get("title"), prop.get("address"), prop.get("price_man"),
                prop.get("area_sqm"), prop.get("tsubo"), prop.get("price_per_tsubo"),
                prop.get("building_condition"), prop.get("url"),
                json.dumps(prop, ensure_ascii=False), now, prop["id"]
            ))
            return "updated"
        else:
            cur.execute("""
                INSERT INTO properties
                    (id, category, title, address, price_man, area_sqm, tsubo,
                     price_per_tsubo, building_condition, url, raw_data, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prop["id"], prop["category"], prop.get("title"), prop.get("address"),
                prop.get("price_man"), prop.get("area_sqm"), prop.get("tsubo"),
                prop.get("price_per_tsubo"), prop.get("building_condition"), prop.get("url"),
                json.dumps(prop, ensure_ascii=False), now, now
            ))
            return "added"

    def deactivate_missing(self, category: str, active_ids: list, include_areas=None, city=None):
        cur = self.conn.cursor()
        now = datetime.now().isoformat()
        if include_areas:
            # 特定エリアのみを対象に削除判定
            area_filter = " OR ".join([f"address LIKE ?" for _ in include_areas])
            area_params = [f"%{a}%" for a in include_areas]
            if active_ids:
                placeholders = ",".join(["?"] * len(active_ids))
                cur.execute(f"""
                    UPDATE properties SET is_active=0, last_seen=?
                    WHERE category=? AND is_active=1
                      AND ({area_filter})
                      AND id NOT IN ({placeholders})
                """, [now, category] + area_params + active_ids)
            else:
                cur.execute(f"""
                    UPDATE properties SET is_active=0, last_seen=?
                    WHERE category=? AND is_active=1 AND ({area_filter})
                """, [now, category] + area_params)
        elif city:
            # 市名でフィルター（諫早市と大村市が同カテゴリでも互いに消さない）
            if not active_ids:
                return 0
            placeholders = ",".join(["?"] * len(active_ids))
            cur.execute(f"""
                UPDATE properties SET is_active=0, last_seen=?
                WHERE category=? AND is_active=1
                  AND address LIKE ?
                  AND id NOT IN ({placeholders})
            """, [now, category, f"%{city}%"] + active_ids)
        else:
            # 全エリア対象（除外エリアを除く通常検索）
            if not active_ids:
                return 0
            placeholders = ",".join(["?"] * len(active_ids))
            cur.execute(f"""
                UPDATE properties SET is_active=0, last_seen=?
                WHERE category=? AND is_active=1 AND id NOT IN ({placeholders})
            """, [now, category] + active_ids)
        self.conn.commit()
        return cur.rowcount

    def log_scan(self, category, total, new_added, removed):
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO scan_history (scanned_at, category, total_found, new_added, removed)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), category, total, new_added, removed))
        self.conn.commit()

    def close(self):
        self.conn.close()


SEARCH_TARGETS = [
    # (category, kind_value, min_price_man, col_map, city, include_areas)
    # include_areas=None は全域（除外エリアのみ除く）
    ("land",    "01", 700,  LAND_COL,  "諫早市", None),
    ("house",   "02", 1000, HOUSE_COL, "諫早市", None),
    ("mansion", "03", 1000, HOUSE_COL, "諫早市", None),
    # 大村市：全域
    ("land",    "01", 700,  LAND_COL,  "大村市", None),
    ("house",   "02", 1000, HOUSE_COL, "大村市", None),
    ("mansion", "03", 1000, HOUSE_COL, "大村市", None),
    # 雲仙市：愛野町・吾妻町のみ
    ("land",    "01", 500,  LAND_COL,  "雲仙市", ["愛野町", "吾妻町"]),
    ("house",   "02", 700,  HOUSE_COL, "雲仙市", ["愛野町", "吾妻町"]),
]


def run_scrape():
    config = load_config()
    OUTPUT_DIR.mkdir(exist_ok=True)

    if config["reins"]["username"] == "YOUR_REINS_USERNAME":
        log.error("config.json のユーザー名とパスワードを設定してください")
        return

    db = ReinsDB()
    driver = get_driver(headless=True)

    try:
        login(driver, config)

        for category, kind_value, min_price, col_map, city, include_areas in SEARCH_TARGETS:
            log.info(f"=== {category} {city} 検索開始（{min_price}万円以上）===")
            driver.get("https://system.reins.jp/main/BK/GBK001210")
            time.sleep(4)

            # 物件種別設定
            set_select_vue(driver, "__BVID__41", kind_value)
            time.sleep(1)

            # 所在地設定
            set_input_vue(driver, "__BVID__94", "長崎県")
            time.sleep(0.3)
            set_input_vue(driver, "__BVID__98", city)
            time.sleep(0.3)

            # 土地の場合：建築条件なしを選択（__BVID__80=建築条件なし）
            if kind_value == "01":
                driver.execute_script("""
                    var el = document.getElementById('__BVID__80');
                    if (el) {
                        el.checked = true;
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                """)

            # 検索実行
            click_search(driver)
            time.sleep(5)

            if "GBK002100" not in driver.current_url:
                log.warning(f"{category}: 検索結果ページに遷移しませんでした: {driver.current_url}")
                continue

            # 全ページ収集
            props = collect_all_pages(driver, category, col_map, min_price,
                                      include_areas=include_areas)
            log.info(f"{category}: {len(props)}件取得（フィルター後）")

            active_ids = []
            new_count = 0

            for prop in props:
                result = db.upsert_property(prop)
                active_ids.append(prop["id"])
                if result == "added":
                    new_count += 1

            removed_count = db.deactivate_missing(category, active_ids, include_areas, city=city if not include_areas else None)
            db.log_scan(category, len(props), new_count, removed_count)
            log.info(f"  新規: {new_count}件 / 削除: {removed_count}件")

            time.sleep(2)

    finally:
        driver.quit()
        db.close()

    log.info("スクレイピング完了")


if __name__ == "__main__":
    run_scrape()
