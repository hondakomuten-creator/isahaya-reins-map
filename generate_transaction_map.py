"""
過去の土地取引価格マップ生成（Leaflet.js + OpenStreetMap）
対象: 諫早市・大村市・雲仙市(愛野町) の土地取引（宅地・住宅地）
国土交通省 不動産取引価格情報をもとに、取引面積・坪単価を地図上にプロットする

※ 取引データは町丁目までの粒度でしか住所が分からないため、同じ町の取引は
   ジオコーディングすると同一座標になる。そのため座標単位でグループ化し、
   1地点1マーカー・ポップアップ内に取引一覧を表示する方式にしている。
"""

import json
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "reins_data.db"
OUTPUT_DIR = BASE_DIR / "output"

LAND_TRADE_TYPES = ("宅地(土地)",)

# 表示・色分け・フィルターの単位（雲仙市は地区で分ける）
AREA_COLORS = {
    "諫早市": "#e74c3c",
    "大村市": "#2980b9",
    "雲仙市(愛野町)": "#27ae60",
    "雲仙市(吾妻町)": "#f39c12",
}

# 市区町村コード（不動産情報ライブラリのエビデンスURL生成用。エリアではなく市区町村単位）
CITY_CODES = {
    "諫早市": "42204",
    "大村市": "42205",
    "雲仙市": "42213",
}


def get_area_key(city, district):
    """表示用のエリアキーを算出する（雲仙市は地区で分ける）"""
    if city == "雲仙市":
        if "愛野" in (district or ""):
            return "雲仙市(愛野町)"
        if "吾妻" in (district or ""):
            return "雲仙市(吾妻町)"
        return "雲仙市(その他)"
    return city


def build_evidence_url(city_name, year, quarter):
    """同一市区町村・同一四半期の検索結果一覧を、不動産情報ライブラリで直接開けるURLを生成
    （地図画面経由だと「市区町村境界クリック→詳細表示」の2手間が必要なため、
    一覧ページ(/realEstatePrices)に直接飛べるURLに変更）"""
    city_code = CITY_CODES.get(city_name)
    if not city_code or not year or not quarter:
        return None
    point_in_time_code = year * 10 + quarter
    return (
        "https://www.reinfolib.mlit.go.jp/realEstatePrices"
        f"?tradeType=2&areaCode={city_code}&region=0&route=0"
        "&priceInformationCategory=01&landType=98"
        f"&from={point_in_time_code}&to={point_in_time_code}"
    )


def get_land_transactions():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in LAND_TRADE_TYPES)
    rows = conn.execute(f"""
        SELECT * FROM past_transactions
        WHERE trade_type IN ({placeholders})
        ORDER BY city, year DESC, quarter DESC
    """, LAND_TRADE_TYPES).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_price(price_man):
    if price_man is None:
        return "価格不明"
    if price_man >= 10000:
        oku = price_man // 10000
        man = price_man % 10000
        return f"{oku}億{man:,}万円" if man else f"{oku}億円"
    return f"{price_man:,}万円"


def build_groups(with_coords):
    """同一座標（同一町）の取引をグループ化する"""
    groups = defaultdict(list)
    for t in with_coords:
        key = (t["latitude"], t["longitude"])
        groups[key].append(t)

    group_list = []
    for (lat, lng), items in groups.items():
        items_sorted = sorted(
            items, key=lambda x: (x.get("year") or 0, x.get("quarter") or 0), reverse=True
        )
        city = items[0].get("city") or "不明"
        district = items[0].get("district") or items[0].get("address") or ""
        area = get_area_key(city, district)
        ppts = [i["price_per_tsubo"] for i in items if i.get("price_per_tsubo")]

        group_list.append({
            "id": f"grp_{lat}_{lng}",
            "city": city,
            "area": area,
            "color": AREA_COLORS.get(area, "#7f8c8d"),
            "lat": lat,
            "lng": lng,
            "district": district,
            "count": len(items),
            "min_ppt": min(ppts) if ppts else None,
            "max_ppt": max(ppts) if ppts else None,
            "avg_ppt": round(sum(ppts) / len(ppts)) if ppts else None,
            "transactions": [
                {
                    "trade_type": i.get("trade_type") or "",
                    "price": format_price(i.get("price_man")),
                    "area_sqm": i.get("area_sqm"),
                    "tsubo": round(i["tsubo"], 1) if i.get("tsubo") else None,
                    "price_per_tsubo": i.get("price_per_tsubo"),
                    "year": i.get("year"),
                    "quarter": i.get("quarter"),
                    "evidence_url": build_evidence_url(city, i.get("year"), i.get("quarter")),
                }
                for i in items_sorted
            ],
        })

    group_list.sort(key=lambda g: (g["area"], g["district"]))
    return group_list


def generate_transaction_map_html(output_path=None):
    transactions = get_land_transactions()

    with_coords = [t for t in transactions if t.get("latitude") and t.get("longitude")]
    no_coords = [t for t in transactions if not t.get("latitude") or not t.get("longitude")]

    if output_path is None:
        output_path = OUTPUT_DIR / "land_transactions_map.html"
    OUTPUT_DIR.mkdir(exist_ok=True)

    groups = build_groups(with_coords)

    area_counts = {}
    for g in groups:
        area_counts[g["area"]] = area_counts.get(g["area"], 0) + g["count"]

    badges = "".join(
        f'<span class="badge">{a} {n}件</span>'
        for a, n in area_counts.items()
    )

    markers_json = json.dumps(groups, ensure_ascii=False)

    no_coords_rows = "".join(
        f'<tr><td>{t.get("city","")}</td><td>{t.get("district","")}</td>'
        f'<td>{format_price(t.get("price_man"))}</td></tr>'
        for t in no_coords
    )
    no_coords_section = ""
    if no_coords:
        no_coords_section = f"""
      <div class="no-coords-box">
        <b>座標未取得 {len(no_coords)}件（地図に未表示）</b>
        <table><tr><th>市</th><th>地区</th><th>価格</th></tr>
        {no_coords_rows}</table>
      </div>"""

    area_filter_buttons = "".join(
        f'<div class="fbtn on" style="border-color:{color};color:{color}" '
        f'data-area="{area}" onclick="toggleArea(this)">{area}</div>'
        for area, color in AREA_COLORS.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>諫早・大村・雲仙(愛野) 土地取引価格マップ</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: "Meiryo", "Noto Sans JP", sans-serif; background: #f0f0f0; }}

#header {{
  background: #2c3e50; color: #fff;
  padding: 10px 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
}}
#header h1 {{ font-size: 1.05rem; margin-right: 8px; }}
.badge {{
  background: rgba(255,255,255,0.18); border-radius: 12px;
  padding: 3px 10px; font-size: 0.8rem;
}}
#updated {{ margin-left: auto; font-size: 0.75rem; opacity: 0.65; }}

#wrap {{ display: flex; height: calc(100vh - 46px); }}

#sidebar {{
  width: 300px; min-width: 260px; background: #fff;
  border-right: 1px solid #ddd; display: flex; flex-direction: column;
  overflow: hidden;
}}
#map {{ flex: 1; z-index: 0; }}

.panel {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
.panel h3 {{ font-size: 0.82rem; color: #666; margin-bottom: 6px; }}
.filter-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.fbtn {{
  padding: 4px 10px; border-radius: 14px; border: 2px solid #ccc;
  cursor: pointer; font-size: 0.78rem; background: #fff; transition: 0.15s;
  user-select: none;
}}
.fbtn.on {{ color: #fff !important; }}

#list {{ flex: 1; overflow-y: auto; padding: 6px; }}
.card {{
  border: 1px solid #e8e8e8; border-radius: 7px; padding: 9px 10px;
  margin-bottom: 6px; cursor: pointer; transition: box-shadow 0.15s;
}}
.card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.12); }}
.cat-tag {{
  display: inline-block; padding: 1px 7px; border-radius: 9px;
  font-size: 0.72rem; color: #fff; margin-bottom: 3px;
}}
.count-tag {{
  display: inline-block; padding: 1px 7px; border-radius: 9px;
  font-size: 0.72rem; color: #555; background: #eee; margin-left: 4px;
}}
.card-price {{ font-size: 1.05rem; font-weight: bold; color: #2c3e50; }}
.card-addr {{ font-size: 0.76rem; color: #888; margin-top: 2px; }}
.card-detail {{ font-size: 0.76rem; color: #555; margin-top: 3px; }}

#min-tsubo-input {{
  width: 90px; padding: 4px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 0.85rem;
}}
.tsubo-unit {{ font-size: 0.8rem; color: #666; align-self: center; margin-left: 4px; }}
#district-search-input {{
  width: 100%; padding: 4px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 0.85rem;
}}

.no-coords-box {{
  margin: 8px; padding: 8px; background: #fff8e1;
  border: 1px solid #ffca28; border-radius: 6px; font-size: 0.76rem;
}}
.no-coords-box table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
.no-coords-box td, .no-coords-box th {{ border: 1px solid #ffe082; padding: 2px 5px; }}

.leaflet-popup-content {{ font-family: "Meiryo", sans-serif; margin: 10px; }}
.leaflet-popup-content-wrapper {{ max-height: 340px; overflow: hidden; }}
.popup-title {{ font-weight: bold; margin-bottom: 6px; color: #2c3e50; }}
.popup-summary {{ font-size: 0.78rem; color: #666; margin-bottom: 8px; }}
.popup-list {{ max-height: 260px; overflow-y: auto; }}
.popup-item {{
  border-top: 1px solid #eee; padding: 6px 0;
}}
.popup-item:first-child {{ border-top: none; }}
.popup-price {{ font-size: 1rem; font-weight: bold; }}
.popup-row {{ font-size: 0.82rem; color: #444; margin: 1px 0; }}
.popup-row span {{ color: #888; }}
.popup-item a {{ font-size: 0.76rem; color: #2980b9; }}

@media (max-width: 768px) {{
  #header {{ padding: 8px 12px; gap: 6px; }}
  #header h1 {{ font-size: 0.92rem; width: 100%; }}
  #updated {{ margin-left: 0; font-size: 0.7rem; }}
  #wrap {{ flex-direction: column; height: auto; min-height: 100vh; }}
  #map {{ height: 90vh; min-height: 400px; width: 100%; flex: none; }}
  #sidebar {{ width: 100%; min-width: unset; height: auto; max-height: 10vh; border-right: none; border-top: 2px solid #ddd; flex-direction: column; }}
  .panel {{ padding: 4px 8px; }}
  .panel h3 {{ display: none; }}
  #list {{ max-height: 32vh; overflow-y: auto; padding: 6px; }}
}}
</style>
</head>
<body>
<div id="header">
  <h1>🗾 諫早・大村・雲仙(愛野) 土地取引価格マップ</h1>
  {badges}
  <span id="updated">更新: {datetime.now().strftime("%Y年%m月%d日 %H:%M")}</span>
</div>
<div id="wrap">
  <div id="sidebar">
    <div class="panel">
      <h3>エリアで絞り込み</h3>
      <div class="filter-row">
        {area_filter_buttons}
      </div>
    </div>
    <div class="panel">
      <h3>面積で絞り込み</h3>
      <div class="filter-row">
        <input type="number" id="min-tsubo-input" placeholder="0" min="0" step="1" oninput="setMinTsubo(this.value)">
        <span class="tsubo-unit">坪以上</span>
      </div>
    </div>
    <div class="panel">
      <h3>町名で検索</h3>
      <div class="filter-row">
        <input type="text" id="district-search-input" placeholder="例: 愛野町" oninput="setDistrictSearch(this.value)">
      </div>
    </div>
    <div id="list"></div>
    {no_coords_section}
  </div>
  <div id="map"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const GROUPS = {markers_json};

const map = L.map("map");
L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{{z}}/{{x}}/{{y}}.png", {{
  attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>',
  maxZoom: 18,
}}).addTo(map);

if (GROUPS.length > 0) {{
  const bounds = L.latLngBounds(GROUPS.map(g => [g.lat, g.lng]));
  map.fitBounds(bounds, {{ padding: [30, 30] }});
}} else {{
  map.setView([32.85, 130.05], 11);
}}

function makeIcon(color, count) {{
  if (count <= 1) {{
    return L.divIcon({{
      className: "",
      html: `<div style="background:${{color}};width:14px;height:14px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.4);"></div>`,
      iconSize: [14, 14], iconAnchor: [7, 7], popupAnchor: [0, -8],
    }});
  }}
  const size = Math.min(20 + Math.round(Math.sqrt(count) * 4), 44);
  return L.divIcon({{
    className: "",
    html: `<div style="background:${{color}};color:#fff;width:${{size}}px;height:${{size}}px;border-radius:50%;
      display:flex;align-items:center;justify-content:center;font-size:${{Math.min(13,9+size/8)}}px;font-weight:bold;
      border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.4);">${{count}}</div>`,
    iconSize: [size, size], iconAnchor: [size/2, size/2], popupAnchor: [0, -size/2],
  }});
}}

let minTsubo = 0;

function filteredTx(g) {{
  if (!minTsubo) return g.transactions;
  return g.transactions.filter(t => t.tsubo != null && t.tsubo >= minTsubo);
}}

function groupSummary(txs) {{
  const ppts = txs.map(t => t.price_per_tsubo).filter(v => v);
  return {{
    count: txs.length,
    min_ppt: ppts.length ? Math.min(...ppts) : null,
    max_ppt: ppts.length ? Math.max(...ppts) : null,
    avg_ppt: ppts.length ? Math.round(ppts.reduce((a,b) => a+b, 0) / ppts.length) : null,
  }};
}}

function buildPopupHtml(g) {{
  const txs = filteredTx(g);
  const s = groupSummary(txs);
  const pptSummary = s.min_ppt
    ? (s.min_ppt === s.max_ppt
        ? `坪単価 ${{s.min_ppt.toLocaleString()}}円`
        : `坪単価 ${{s.min_ppt.toLocaleString()}}〜${{s.max_ppt.toLocaleString()}}円（平均${{s.avg_ppt.toLocaleString()}}円）`)
    : "";
  const items = txs.map(t => {{
    const sqmStr = t.area_sqm ? `${{t.area_sqm.toFixed(1)}}㎡` : "";
    const tsuboStr = t.tsubo ? ` / ${{t.tsubo}}坪` : "";
    const ptStr = t.price_per_tsubo ? `<div class="popup-row"><span>坪単価：</span><b>${{t.price_per_tsubo.toLocaleString()}}円</b></div>` : "";
    const timeStr = (t.year && t.quarter) ? `${{t.year}}年 第${{t.quarter}}四半期` : "";
    const evidenceStr = t.evidence_url
      ? `<div style="margin-top:3px">
          <a href="${{t.evidence_url}}" target="_blank" rel="noopener">国交省サイトで確認 →</a>
          <div style="color:#999;font-size:0.68rem;margin-top:1px">
            ※市区町村全体の一覧が開きます。「${{g.district}}」だけに絞り込むには：
            ブラウザ幅を広げてテーブル表示にし、「所在地▼」をクリック→
            「データの絞り込み」欄に「${{g.district}}」と入力→「決定」
          </div>
        </div>`
      : "";
    return `<div class="popup-item">
      <div class="popup-price" style="color:${{g.color}}">${{t.price}}</div>
      ${{sqmStr ? `<div class="popup-row"><span>取引面積：</span>${{sqmStr}}${{tsuboStr}}</div>` : ""}}
      ${{ptStr}}
      ${{timeStr ? `<div class="popup-row"><span>取引時期：</span>${{timeStr}}</div>` : ""}}
      ${{evidenceStr}}
    </div>`;
  }}).join("");
  return `
    <div class="popup-title">${{g.area}} ${{g.district}}（${{s.count}}件）</div>
    <div class="popup-summary">${{pptSummary}}</div>
    <div class="popup-list">${{items}}</div>
  `;
}}

const GROUP_MAP = {{}};
GROUPS.forEach(g => {{ GROUP_MAP[g.id] = g; }});

const txMarkers = {{}};
GROUPS.forEach(g => {{
  const marker = L.marker([g.lat, g.lng], {{ icon: makeIcon(g.color, g.count) }});
  marker.bindPopup(() => buildPopupHtml(g), {{ maxWidth: 300 }});
  txMarkers[g.id] = {{ marker, area: g.area }};
  marker.addTo(map);
}});

const areaFilter = {{}};
{"; ".join(f'areaFilter["{a}"] = true' for a in AREA_COLORS.keys())};

let districtSearch = "";

function matchesDistrict(g) {{
  if (!districtSearch) return true;
  return g.district.includes(districtSearch);
}}

function applyVisibility() {{
  Object.entries(txMarkers).forEach(([id, obj]) => {{
    const g = GROUP_MAP[id];
    const count = filteredTx(g).length;
    if (count > 0) {{
      obj.marker.setIcon(makeIcon(g.color, count));
    }}
    const show = areaFilter[obj.area] !== false && count > 0 && matchesDistrict(g);
    show ? obj.marker.addTo(map) : map.removeLayer(obj.marker);
  }});
}}

function setDistrictSearch(value) {{
  districtSearch = value.trim();
  applyVisibility();
  renderList();
}}

function toggleArea(btn) {{
  const area = btn.dataset.area;
  areaFilter[area] = !areaFilter[area];
  btn.classList.toggle("on");
  if (areaFilter[area]) {{
    btn.style.background = btn.style.borderColor;
  }} else {{
    btn.style.background = "#fff";
  }}
  applyVisibility();
  renderList();
}}

function setMinTsubo(value) {{
  minTsubo = parseFloat(value) || 0;
  applyVisibility();
  renderList();
}}

function renderList() {{
  const el = document.getElementById("list");
  const visible = GROUPS
    .filter(g => areaFilter[g.area] !== false && matchesDistrict(g))
    .map(g => ({{ g, txs: filteredTx(g) }}))
    .filter(({{ txs }}) => txs.length > 0);
  el.innerHTML = visible.map(({{ g, txs }}) => {{
    const s = groupSummary(txs);
    const ppt = s.min_ppt
      ? (s.min_ppt === s.max_ppt
          ? `坪単価 ${{s.min_ppt.toLocaleString()}}円`
          : `坪単価 ${{s.min_ppt.toLocaleString()}}〜${{s.max_ppt.toLocaleString()}}円`)
      : "";
    return `<div class="card" onclick="focusTx('${{g.id}}')">
      <span class="cat-tag" style="background:${{g.color}}">${{g.area}}</span>
      <span class="count-tag">${{s.count}}件</span>
      <div class="card-addr" style="margin-top:4px">${{g.district}}</div>
      ${{ppt ? `<div class="card-detail">${{ppt}}</div>` : ""}}
    </div>`;
  }}).join("");
}}

function focusTx(id) {{
  const obj = txMarkers[id];
  if (!obj) return;
  map.setView(obj.marker.getLatLng(), 16);
  obj.marker.openPopup();
}}

// 初期表示: フィルターボタンの背景色を反映
document.querySelectorAll('.fbtn[data-area]').forEach(btn => {{
  btn.style.background = btn.style.borderColor;
}});

renderList();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"土地取引マップHTML生成: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = generate_transaction_map_html()
    print(f"出力: {path}")
