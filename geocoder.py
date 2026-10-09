"""
住所 → 緯度経度変換（国土地理院ジオコーダー使用・無料・登録不要）
"""

import time
import sqlite3
import logging
import urllib.request
import urllib.parse
import json
from pathlib import Path

log = logging.getLogger(__name__)

DB_FILE = Path(__file__).parent / "reins_data.db"
GEOCODE_API = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
GSI_API = "https://msearch.gsi.go.jp/address-search/AddressSearch"

_cache = {}

# 長崎県のおおよその座標範囲（範囲外なら別の地名と誤認した可能性）
_NAGASAKI_LAT_MIN = 32.4
_NAGASAKI_LAT_MAX = 34.7
_NAGASAKI_LNG_MIN = 128.3
_NAGASAKI_LNG_MAX = 131.2


def _is_in_nagasaki(lat: float, lng: float) -> bool:
    return (_NAGASAKI_LAT_MIN <= lat <= _NAGASAKI_LAT_MAX and
            _NAGASAKI_LNG_MIN <= lng <= _NAGASAKI_LNG_MAX)


def _gsi_query(query: str) -> tuple[float | None, float | None]:
    """国土地理院APIに問い合わせて (lat, lng) を返す。失敗は (None, None)。"""
    try:
        params = urllib.parse.urlencode({"q": query})
        url = f"{GSI_API}?{params}"
        with urllib.request.urlopen(url, timeout=10) as res:
            data = json.loads(res.read().decode("utf-8"))
        if data and len(data) > 0:
            lat = float(data[0]["geometry"]["coordinates"][1])
            lng = float(data[0]["geometry"]["coordinates"][0])
            return lat, lng
    except Exception as e:
        log.debug(f"GSI APIエラー ({query}): {e}")
    return None, None


def geocode_address(address: str, default_prefix: str = "長崎県諫早市") -> tuple[float | None, float | None]:
    """住所を緯度経度に変換（国土地理院API使用）。
    長崎県外の座標が返った場合、住所を短くしながら再試行する。
    """
    if not address:
        return None, None
    if address in _cache:
        return _cache[address]

    # 都道府県・市区町村名が含まれていなければデフォルトを付加して検索
    base = address if ("県" in address or "市" in address) else f"{default_prefix}{address}"

    # 段階的に試みる候補を作成（長い住所 → 徐々に短く）
    # 例: "諫早市飯盛町久保" → "長崎県諫早市飯盛町久保" → "長崎県諫早市飯盛町" → "長崎県諫早市"
    candidates = [base]
    # 「長崎県」が先頭にない場合は付加した版も追加
    if not base.startswith("長崎県"):
        candidates.append(f"長崎県{base}")
    # 末尾から字・番地を削って短くしていく
    import re
    shorter = base
    for _ in range(3):
        # 末尾の数字や字レベル（久保、○○町など）を除去
        new = re.sub(r'[\d\-番地号の]+$', '', shorter).rstrip()
        if new == shorter:
            new = re.sub(r'[^\s市区町村]+$', '', shorter).rstrip()
        if not new or new == shorter:
            break
        shorter = new
        if shorter not in candidates:
            candidates.append(shorter)
        if not shorter.startswith("長崎県"):
            alt = f"長崎県{shorter}"
            if alt not in candidates:
                candidates.append(alt)

    for query in candidates:
        lat, lng = _gsi_query(query)
        if lat is not None:
            if _is_in_nagasaki(lat, lng):
                log.debug(f"ジオコーディング成功 [{query}] → ({lat}, {lng})")
                _cache[address] = (lat, lng)
                return lat, lng
            else:
                log.debug(f"長崎県外の座標のため再試行 [{query}] → ({lat}, {lng})")

    log.debug(f"ジオコーディング失敗: {address}")
    _cache[address] = (None, None)
    return None, None


def geocode_all_properties():
    """DBの未ジオコード物件を一括処理"""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, address FROM properties WHERE latitude IS NULL AND address != ''"
    ).fetchall()
    log.info(f"ジオコーディング対象: {len(rows)}件")

    updated = 0
    for prop_id, address in rows:
        lat, lng = geocode_address(address)
        if lat:
            conn.execute(
                "UPDATE properties SET latitude=?, longitude=? WHERE id=?",
                (lat, lng, prop_id)
            )
            updated += 1
        time.sleep(0.3)  # APIレート制限対応

    conn.commit()
    conn.close()
    log.info(f"ジオコーディング完了: {updated}件")


def geocode_all_transactions():
    """過去取引データのジオコーディング（市区町村＋地区名ベース）"""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT DISTINCT city, district FROM past_transactions "
        "WHERE latitude IS NULL AND district != ''"
    ).fetchall()
    log.info(f"地区ジオコーディング対象: {len(rows)}件")

    for city, district in rows:
        city_name = city or "諫早市"
        lat, lng = geocode_address(f"長崎県{city_name}{district}")
        if lat:
            conn.execute(
                "UPDATE past_transactions SET latitude=?, longitude=? "
                "WHERE district=? AND (city=? OR (city IS NULL AND ?=''))",
                (lat, lng, district, city, city or "")
            )
        time.sleep(0.3)

    conn.commit()
    conn.close()
