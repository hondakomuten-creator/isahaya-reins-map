"""
長崎県宅地建物取引業協会（たっけんくんネット）スクレイパー
- 売り土地（700万円以上）
- 売り中古住宅（1000万円以上）
- 売りマンション（1000万円以上）
諫早市を対象。緯度経度はGoogleマップリンクから直接取得。
"""

import json
import re
import time
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "reins_data.db"
BASE_URL = "https://www.n-takken.or.jp"

_cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
# 先頭ゼロを除いた数字で比較（"00581008" → "581008"）
EXCLUDED_PROPERTY_NOS = set(s.lstrip("0") for s in _cfg.get("excluded_property_nos", []))

EXCLUDE_AREAS = [
    "正久寺町", "高天町", "白浜町", "高来町",
    "小長井町", "目代町", "多良見町",
]

# 種別ごとの検索設定
# (category, ptm_code, min_price_yen, label, city_code, include_areas)
SEARCH_TARGETS = [
    # 諫早市
    ("land_tk",    "0101", 7000000,  "土地",     "42204", None),
    ("house_tk",   "9102", 10000000, "中古住宅", "42204", None),
    ("mansion_tk", "0103", 10000000, "マンション","42204", None),
    # 雲仙市：愛野町・吾妻町のみ
    ("land_tk",    "0101", 5000000,  "土地",     "42213", ["愛野町", "吾妻町"]),
    ("house_tk",   "9102", 7000000,  "中古住宅", "42213", ["愛野町", "吾妻町"]),
]


def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def parse_price_man(text):
    if not text:
        return None
    text = text.replace(",", "").replace("万円", "").strip()
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return None


def parse_area_sqm(text):
    """'557.12㎡(約168.53坪)' → 557.12"""
    if not text:
        return None
    m = re.search(r"([\d,\.]+)[㎡m²]", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def sqm_to_tsubo(sqm):
    if sqm is None:
        return None
    return round(sqm / 3.30579, 2)


def scrape_page(driver, url):
    """1ページ分の物件データを取得"""
    driver.get(url)
    time.sleep(2)

    props = driver.execute_script("""
        var results = [];
        var blocks = document.querySelectorAll('.form-group.prop-fg');

        // ページ全体のstrong（価格）をインデックス順に取得
        // 先頭に「並べ替え」などの余分なstrongがあるので価格らしいもの(万円含む)のみ抽出
        var allPriceTexts = Array.from(document.querySelectorAll('strong'))
            .map(s => s.textContent.trim())
            .filter(t => t.includes('万円'));

        blocks.forEach(function(block, idx) {
            try {
                var prop = {};

                // 物件番号
                var pno = block.querySelector('input[name="p_no[]"]');
                prop.p_no = pno ? pno.value : null;
                if (!prop.p_no) return;

                // 所在地（種別＋住所）
                var titleLinks = block.querySelectorAll('span.prop-title-link');
                var titleTexts = Array.from(titleLinks).map(s => s.textContent.trim()).filter(t => t);

                // 建築条件付きチェック（タイトルまたは説明文に含まれる場合はスキップ）
                var fullText = block.textContent || '';
                if (fullText.includes('建築条件付') || fullText.includes('建条付')) return;
                prop.title_raw = titleTexts.join(' ');

                // 緯度経度（Google MapsリンクのURLから）
                var mapLink = block.querySelector('a.map-icon[href*="maps"]');
                if (mapLink) {
                    var m = mapLink.href.match(/q=([\d.\-]+),([\d.\-]+)/);
                    if (m) {
                        prop.lat = parseFloat(m[1]);
                        prop.lng = parseFloat(m[2]);
                    }
                }

                // 価格：インデックス対応（各ブロックに1つの価格が順番に対応）
                prop.price_text = idx < allPriceTexts.length ? allPriceTexts[idx] : '';

                // 面積
                var areaIcon = Array.from(block.querySelectorAll('span.icon.info'))
                    .find(s => s.textContent.includes('土地面積') || s.textContent.includes('専有面積') || s.textContent.includes('面積'));
                if (areaIcon) {
                    var valueEl = areaIcon.nextElementSibling;
                    while (valueEl && !valueEl.classList.contains('value')) {
                        valueEl = valueEl.nextElementSibling;
                    }
                    prop.area_text = valueEl ? valueEl.textContent.trim() : '';
                }

                // アクセス（駅からの距離など）
                var accessEl = block.querySelector('.access');
                prop.access = accessEl ? accessEl.textContent.trim() : '';

                results.push(prop);
            } catch(e) {}
        });

        return results;
    """)

    return props or []


def collect_category(driver, category, ptm, min_price_yen, label, city_code="42204", include_areas=None):
    """全ページ収集。include_areas指定時はその町名のみ取得。"""
    results = []
    page = 1

    while True:
        url = (
            f"{BASE_URL}/property/buy/area/list"
            f"?ptm%5B%5D={ptm}&m_adr%5B%5D={city_code}"
            f"&price_b_from={min_price_yen}&page={page}"
        )
        log.info(f"  ページ{page}: {url[-60:]}")
        props = scrape_page(driver, url)

        if not props:
            break

        for p in props:
            title = p.get("title_raw", "")
            address = re.sub(r"^【[^】]+】\s*", "", title).strip()

            # エリアフィルター
            if include_areas:
                if not any(inc in address for inc in include_areas):
                    continue
            elif any(ex in address for ex in EXCLUDE_AREAS):
                continue

            # 手動除外リスト（建築条件付き等）
            raw_pno = p["p_no"].lstrip("0")
            if raw_pno in EXCLUDED_PROPERTY_NOS:
                log.info(f"    除外リスト: {p['p_no']} をスキップ")
                continue

            price_man = parse_price_man(p.get("price_text", ""))
            area_sqm = parse_area_sqm(p.get("area_text", ""))
            tsubo = sqm_to_tsubo(area_sqm)
            price_per_tsubo = int(price_man * 10000 / tsubo) if (price_man and tsubo) else None

            results.append({
                "id": f"{category}_{p['p_no']}",
                "category": category,
                "title": p["p_no"],
                "address": address,
                "price_man": price_man,
                "area_sqm": area_sqm,
                "tsubo": tsubo,
                "price_per_tsubo": price_per_tsubo,
                "building_condition": "",
                "latitude": p.get("lat"),
                "longitude": p.get("lng"),
                "url": f"{BASE_URL}/property/detail?p_no={p['p_no']}",
            })

        log.info(f"    {len(props)}件取得（累計{len(results)}件）")

        # 次ページ確認
        has_next = driver.execute_script("""
            var links = document.querySelectorAll('[class*="pager"] a, [class*="pagination"] a, .pager a');
            var nextPage = arguments[0] + 1;
            return Array.from(links).some(a => a.textContent.trim() === String(nextPage));
        """, page)

        if not has_next:
            break
        page += 1
        time.sleep(1)

    return results


class TakkenDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self._init_table()

    def _init_table(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT,
                address TEXT, price_man INTEGER, area_sqm REAL, tsubo REAL,
                price_per_tsubo INTEGER, building_condition TEXT,
                latitude REAL, longitude REAL, url TEXT, raw_data TEXT,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
                category TEXT, total_found INTEGER, new_added INTEGER, removed INTEGER
            );
        """)
        self.conn.commit()

    def upsert(self, prop):
        now = datetime.now().isoformat()
        existing = self.conn.execute(
            "SELECT id FROM properties WHERE id=?", (prop["id"],)
        ).fetchone()
        if existing:
            self.conn.execute("""
                UPDATE properties SET
                    title=?, address=?, price_man=?, area_sqm=?, tsubo=?,
                    price_per_tsubo=?, latitude=?, longitude=?, url=?,
                    raw_data=?, last_seen=?, is_active=1
                WHERE id=?
            """, (
                prop.get("title"), prop.get("address"), prop.get("price_man"),
                prop.get("area_sqm"), prop.get("tsubo"), prop.get("price_per_tsubo"),
                prop.get("latitude"), prop.get("longitude"), prop.get("url"),
                json.dumps(prop, ensure_ascii=False), now, prop["id"]
            ))
            return "updated"
        else:
            self.conn.execute("""
                INSERT INTO properties
                    (id, category, title, address, price_man, area_sqm, tsubo,
                     price_per_tsubo, building_condition, latitude, longitude,
                     url, raw_data, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prop["id"], prop["category"], prop.get("title"), prop.get("address"),
                prop.get("price_man"), prop.get("area_sqm"), prop.get("tsubo"),
                prop.get("price_per_tsubo"), prop.get("building_condition"),
                prop.get("latitude"), prop.get("longitude"), prop.get("url"),
                json.dumps(prop, ensure_ascii=False), now, now
            ))
            return "added"

    def deactivate_missing(self, category, active_ids, include_areas=None):
        now = datetime.now().isoformat()
        if include_areas:
            area_filter = " OR ".join([f"address LIKE ?" for _ in include_areas])
            area_params = [f"%{a}%" for a in include_areas]
            if active_ids:
                ph = ",".join(["?"] * len(active_ids))
                cur = self.conn.execute(
                    f"UPDATE properties SET is_active=0, last_seen=? "
                    f"WHERE category=? AND is_active=1 AND ({area_filter}) AND id NOT IN ({ph})",
                    [now, category] + area_params + active_ids
                )
            else:
                cur = self.conn.execute(
                    f"UPDATE properties SET is_active=0, last_seen=? "
                    f"WHERE category=? AND is_active=1 AND ({area_filter})",
                    [now, category] + area_params
                )
        else:
            if not active_ids:
                return 0
            ph = ",".join(["?"] * len(active_ids))
            cur = self.conn.execute(
                f"UPDATE properties SET is_active=0, last_seen=? "
                f"WHERE category=? AND is_active=1 AND id NOT IN ({ph})",
                [now, category] + active_ids
            )
        self.conn.commit()
        return cur.rowcount

    def log_scan(self, category, total, new_added, removed):
        self.conn.execute(
            "INSERT INTO scan_history (scanned_at, category, total_found, new_added, removed) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(), category, total, new_added, removed)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


def run_scrape():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("ntakken_scraper.log", encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    db = TakkenDB()
    driver = get_driver()

    try:
        for category, ptm, min_price, label, city_code, include_areas in SEARCH_TARGETS:
            city_name = "雲仙市" if city_code == "42213" else "諫早市"
            area_str = f"（{'・'.join(include_areas)}）" if include_areas else ""
            log.info(f"=== たっけんくん: {city_name}{area_str} {label} 検索開始 ===")
            props = collect_category(driver, category, ptm, min_price, label,
                                     city_code=city_code, include_areas=include_areas)
            log.info(f"  合計取得: {len(props)}件")

            active_ids = []
            new_count = 0
            for prop in props:
                result = db.upsert(prop)
                active_ids.append(prop["id"])
                if result == "added":
                    new_count += 1

            removed = db.deactivate_missing(category, active_ids, include_areas)
            db.log_scan(category, len(props), new_count, removed)
            log.info(f"  新規: {new_count}件 / 削除: {removed}件")
            time.sleep(1)

    finally:
        driver.quit()
        db.close()

    log.info("たっけんくんスクレイピング完了")


if __name__ == "__main__":
    run_scrape()
