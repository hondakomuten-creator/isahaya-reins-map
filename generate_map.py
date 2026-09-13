"""
Leaflet.js + OpenStreetMap による地図HTML生成
- 完全無料・APIキー不要
- 現在販売中の物件をピン表示
"""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

NEW_DAYS = 14  # 何日以内をNEW扱いにするか
# ※初回一括取得（2026-05-29）はNEW対象外にするため、
#   2026-06-01以降に追加された物件のみをNEW扱いにする
NEW_CUTOFF = "2026-06-01"

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "reins_data.db"
OUTPUT_DIR = BASE_DIR / "output"

CATEGORY_LABELS = {
    "land":       "売り土地（REINS）",
    "house":      "中古住宅（REINS）",
    "mansion":    "マンション（REINS）",
    "land_tk":    "売り土地（宅建）",
    "house_tk":   "中古住宅（宅建）",
    "mansion_tk": "マンション（宅建）",
}

CATEGORY_COLORS = {
    "land":       "#e74c3c",
    "house":      "#27ae60",
    "mansion":    "#2980b9",
    "land_tk":    "#c0392b",
    "house_tk":   "#1e8449",
    "mansion_tk": "#1a5276",
}

CATEGORY_ICONS = {
    "land":       "🌿",
    "house":      "🏠",
    "mansion":    "🏢",
    "land_tk":    "🌱",
    "house_tk":   "🏡",
    "mansion_tk": "🏬",
}



def get_active_properties():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM properties WHERE is_active=1 ORDER BY category, price_man"
    ).fetchall()
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


def generate_map_html(output_path=None):
    properties = get_active_properties()

    props_with_coords = [p for p in properties if p.get("latitude") and p.get("longitude")]
    props_no_coords = [p for p in properties if not p.get("latitude") or not p.get("longitude")]

    if output_path is None:
        output_path = OUTPUT_DIR / "isahaya_reins_map.html"

    OUTPUT_DIR.mkdir(exist_ok=True)

    stats = {
        "land":    sum(1 for p in properties if p["category"] in ("land", "land_tk")),
        "house":   sum(1 for p in properties if p["category"] in ("house", "house_tk")),
        "mansion": sum(1 for p in properties if p["category"] in ("mansion", "mansion_tk")),
        "updated": datetime.now().strftime("%Y年%m月%d日 %H:%M"),
    }

    markers_json = json.dumps([
        {
            "id": p["id"],
            "category": p["category"],
            "label": CATEGORY_LABELS.get(p["category"], p["category"]),
            "color": CATEGORY_COLORS.get(p["category"], "#999"),
            "icon": CATEGORY_ICONS.get(p["category"], "📍"),
            "lat": p["latitude"],
            "lng": p["longitude"],
            "address": p.get("address", ""),
            "price": format_price(p.get("price_man")),
            "price_man": p.get("price_man"),
            "area_sqm": p.get("area_sqm"),
            "tsubo": round(p["tsubo"], 1) if p.get("tsubo") else None,
            "price_per_tsubo": p.get("price_per_tsubo"),
            "building_condition": p.get("building_condition", ""),
            "first_seen": (p.get("first_seen") or "")[:10],
            "url": p.get("url", ""),
            "source": "宅建" if p["category"].endswith("_tk") else "REINS",
            "is_new": (
                bool(p.get("first_seen")) and
                p["first_seen"][:10] >= NEW_CUTOFF and
                datetime.fromisoformat(p["first_seen"][:19]) >= datetime.now() - timedelta(days=NEW_DAYS)
            ),
        }
        for p in props_with_coords
    ], ensure_ascii=False)

    no_coords_rows = ""
    for p in props_no_coords:
        label = CATEGORY_LABELS.get(p["category"], p["category"])
        no_coords_rows += f'<tr><td>{label}</td><td>{p.get("address","(住所なし)")}</td><td>{format_price(p.get("price_man"))}</td></tr>'

    no_coords_section = ""
    if props_no_coords:
        no_coords_section = f"""
      <div class="no-coords-box">
        <b>⚠ 座標未取得 {len(props_no_coords)}件（地図に未表示）</b>
        <table><tr><th>種別</th><th>住所</th><th>価格</th></tr>
        {no_coords_rows}</table>
      </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>諫早市 不動産情報マップ</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: "Meiryo", "Noto Sans JP", sans-serif; background: #f0f0f0; }}

#header {{
  background: #2c3e50; color: #fff;
  padding: 10px 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
}}
#header h1 {{ font-size: 1.1rem; margin-right: 8px; }}
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
.fbtn.on.land    {{ background: #e74c3c; border-color: #e74c3c; color: #fff; }}
.fbtn.on.house   {{ background: #27ae60; border-color: #27ae60; color: #fff; }}
.fbtn.on.mansion {{ background: #2980b9; border-color: #2980b9; color: #fff; }}
.fbtn.land    {{ border-color: #e74c3c; color: #e74c3c; }}
.fbtn.house   {{ border-color: #27ae60; color: #27ae60; }}
.fbtn.mansion {{ border-color: #2980b9; color: #2980b9; }}
.source-tag {{ font-size: 0.68rem; padding: 1px 5px; border-radius: 8px; margin-left: 4px; }}
.source-reins {{ background: #ecf0f1; color: #555; }}
.source-takken {{ background: #fef9e7; color: #7d6608; border: 1px solid #f0ca4d; }}

/* カラーマーク */
#color-panel {{
  padding: 8px 12px; border-bottom: 1px solid #eee; background: #fafafa;
}}
#color-panel h3 {{ font-size: 0.82rem; color: #666; margin-bottom: 6px; }}
.color-row {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
.color-swatch {{
  width: 28px; height: 28px; border-radius: 50%; cursor: pointer;
  border: 3px solid transparent; transition: 0.15s;
  flex-shrink: 0;
}}
.color-swatch.selected {{ border-color: #333; transform: scale(1.15); }}
.color-swatch.none {{ background: #eee; border: 2px dashed #aaa; position: relative; }}
.color-swatch.none::after {{ content: '×'; position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); font-size: 14px; color: #999; }}
#color-label-input {{
  font-size: 0.75rem; border: 1px solid #ddd; border-radius: 6px;
  padding: 3px 6px; width: 80px;
}}
.mark-dot {{
  display: inline-block; width: 10px; height: 10px;
  border-radius: 50%; margin-left: 4px; vertical-align: middle;
  flex-shrink: 0;
}}
.card-mark {{ float: right; margin-left: 4px; }}
.fbtn.on.fav        {{ background: #7f8c8d; border-color: #7f8c8d; color: #fff; }}
.fbtn.fav           {{ border-color: #7f8c8d; color: #7f8c8d; }}
.fbtn.on.new-filter {{ background: #e74c3c; border-color: #e74c3c; color: #fff; }}
.fbtn.new-filter    {{ border-color: #e74c3c; color: #e74c3c; }}
.fbtn.on.reins  {{ background: #2471a3; border-color: #2471a3; color: #fff; }}
.fbtn.reins     {{ border-color: #2471a3; color: #2471a3; }}
.fbtn.on.takken {{ background: #b7950b; border-color: #b7950b; color: #fff; }}
.fbtn.takken    {{ border-color: #b7950b; color: #b7950b; }}

/* カラーフィルター */
#color-filter-row {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }}
.cfbtn {{
  display: flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 12px; border: 2px solid #ddd;
  cursor: pointer; font-size: 0.75rem; background: #fff;
  user-select: none; transition: 0.15s;
}}
.cfbtn.active {{ border-color: #333; background: #f0f0f0; font-weight: bold; }}

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
.card-price {{ font-size: 1.05rem; font-weight: bold; color: #2c3e50; }}
.card-addr {{ font-size: 0.76rem; color: #888; margin-top: 2px; }}
.card-detail {{ font-size: 0.76rem; color: #555; margin-top: 3px; }}

.no-coords-box {{
  margin: 8px; padding: 8px; background: #fff8e1;
  border: 1px solid #ffca28; border-radius: 6px; font-size: 0.76rem;
}}
.no-coords-box table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
.no-coords-box td, .no-coords-box th {{
  border: 1px solid #ffe082; padding: 2px 5px;
}}

.leaflet-popup-content {{ font-family: "Meiryo", sans-serif; }}
.popup-title {{ font-weight: bold; margin-bottom: 5px; color: #2c3e50; }}
.popup-price {{ font-size: 1.1rem; font-weight: bold; margin-bottom: 4px; }}
.popup-row {{ font-size: 0.85rem; color: #444; margin: 2px 0; }}
.popup-row span {{ color: #888; }}
.popup-propno {{ font-size: 0.75rem; color: #aaa; margin-top: 6px; }}
.new-badge {{
  display: inline-block; background: #e74c3c; color: #fff;
  font-size: 0.65rem; font-weight: bold; padding: 1px 5px;
  border-radius: 4px; margin-left: 4px; vertical-align: middle;
  letter-spacing: 0.05em;
}}

/* ===== スマホ対応 (768px以下) ===== */
@media (max-width: 768px) {{
  #header {{ padding: 8px 12px; gap: 6px; }}
  #header h1 {{ font-size: 0.95rem; width: 100%; }}
  .badge {{ font-size: 0.72rem; padding: 2px 8px; }}
  #updated {{ margin-left: 0; font-size: 0.7rem; }}

  /* 縦並び：地図を上、サイドバーを下 */
  #wrap {{ flex-direction: column; height: auto; min-height: 100vh; }}

  #map {{
    height: 90vh;
    min-height: 400px;
    width: 100%;
    flex: none;
  }}

  #sidebar {{
    width: 100%;
    min-width: unset;
    height: auto;
    max-height: 10vh;
    border-right: none;
    border-top: 2px solid #ddd;
    flex-direction: column;
  }}

  .panel {{ padding: 4px 8px; }}
  .panel h3 {{ display: none; }}

  /* フィルターボタン */
  .fbtn {{
    padding: 4px 10px;
    font-size: 0.78rem;
    border-radius: 14px;
  }}

  #list {{ max-height: 32vh; overflow-y: auto; padding: 6px; }}

  /* カードを横スクロール対応 */
  .card {{ padding: 8px 10px; margin-bottom: 5px; }}
  .card-price {{ font-size: 1rem; }}
  .card-addr {{ font-size: 0.78rem; }}

  /* ポップアップを大きく */
  .leaflet-popup-content {{
    font-size: 0.9rem;
    min-width: 200px;
  }}
  .popup-price {{ font-size: 1.15rem; }}
  .popup-row {{ font-size: 0.88rem; }}
}}
</style>
</head>
<body>
<div id="header">
  <h1>🏘 諫早市 不動産情報マップ</h1>
  <span class="badge">🌿 土地 {stats['land']}件</span>
  <span class="badge">🏠 中古住宅 {stats['house']}件</span>
  <span class="badge">🏢 マンション {stats['mansion']}件</span>
  <span id="updated">更新: {stats['updated']}</span>
</div>
<div id="wrap">
  <div id="sidebar">
    <div class="panel">
      <h3>表示切替</h3>
      <div class="filter-row">
        <div class="fbtn on land"    data-cat="land"    onclick="toggle(this)">🌿 土地</div>
        <div class="fbtn on house"   data-cat="house"   onclick="toggle(this)">🏠 住宅</div>
        <div class="fbtn on mansion" data-cat="mansion" onclick="toggle(this)">🏢 マンション</div>
        <div class="fbtn fav"        data-cat="fav"     onclick="toggle(this)">🏷 マーク済み</div>
        <div class="fbtn new-filter" data-cat="new"     onclick="toggle(this)">🆕 NEW</div>
      </div>
      <div class="filter-row" style="margin-top:6px;">
        <div class="fbtn on reins"  data-cat="reins"  onclick="toggle(this)">🔵 REINS</div>
        <div class="fbtn on takken" data-cat="takken" onclick="toggle(this)">🟡 たっけんくん</div>
      </div>
    </div>

    <div id="color-panel">
      <h3>🎨 マーク色を選択</h3>
      <div class="color-row">
        <div class="color-swatch none selected" data-color="" onclick="selectColor(this)" title="解除"></div>
        <div class="color-swatch" style="background:#e74c3c" data-color="#e74c3c" onclick="selectColor(this)" title="赤"></div>
        <div class="color-swatch" style="background:#e67e22" data-color="#e67e22" onclick="selectColor(this)" title="オレンジ"></div>
        <div class="color-swatch" style="background:#f1c40f" data-color="#f1c40f" onclick="selectColor(this)" title="黄"></div>
        <div class="color-swatch" style="background:#27ae60" data-color="#27ae60" onclick="selectColor(this)" title="緑"></div>
        <div class="color-swatch" style="background:#2980b9" data-color="#2980b9" onclick="selectColor(this)" title="青"></div>
        <div class="color-swatch" style="background:#8e44ad" data-color="#8e44ad" onclick="selectColor(this)" title="紫"></div>
        <input id="color-label-input" type="text" placeholder="名前(任意)" maxlength="6">
      </div>
      <div id="color-filter-row"></div>
    </div>

    <div id="list"></div>
    {no_coords_section}
  </div>
  <div id="map"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const PROPS = {markers_json};
const COLORS = {{
  land:"#e74c3c", house:"#27ae60", mansion:"#2980b9",
  land_tk:"#c0392b", house_tk:"#1e8449", mansion_tk:"#1a5276"
}};
const LABELS = {{
  land:"売り土地", house:"中古住宅", mansion:"マンション",
  land_tk:"売り土地", house_tk:"中古住宅", mansion_tk:"マンション"
}};
// フィルターグループ（REINSとたっけんくんを同じボタンで制御）
const CAT_GROUP = {{
  land: "land", land_tk: "land",
  house: "house", house_tk: "house",
  mansion: "mansion", mansion_tk: "mansion"
}};

const map = L.map("map").setView([32.8398, 130.0551], 13);
L.tileLayer("https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png", {{
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/">CARTO</a>',
  subdomains: 'abcd',
  maxZoom: 19,
}}).addTo(map);

function makeIcon(color, emoji, isNew) {{
  const newBadge = isNew
    ? `<div style="position:absolute;top:-6px;right:-8px;background:#e74c3c;color:#fff;font-size:8px;font-weight:bold;padding:1px 3px;border-radius:3px;line-height:1.4;white-space:nowrap;border:1px solid #fff;">NEW</div>`
    : '';
  return L.divIcon({{
    className: "",
    html: `<div style="position:relative;width:28px;height:28px">
      <div style="background:${{color}};color:#fff;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:14px;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.4);">${{emoji}}</div>
      ${{newBadge}}
    </div>`,
    iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -16],
  }});
}}

// 物件データをIDで引けるようにグローバル管理
const PROP_MAP = {{}};
PROPS.forEach(p => {{ PROP_MAP[p.id] = p; }});

// ポップアップHTMLをグローバル関数として定義（マーク変更後の再描画用）
function buildPopupHtml(pid) {{
  const p = PROP_MAP[pid];
  if (!p) return '';
  const sqmStr = p.area_sqm ? `${{p.area_sqm.toFixed(1)}}㎡` : "";
  const tsuboStr = p.tsubo ? ` / ${{p.tsubo}}坪` : "";
  const ptStr = p.price_per_tsubo ? `<div class="popup-row"><span>坪単価：</span><b>${{p.price_per_tsubo.toLocaleString()}}円</b></div>` : "";
  const bcStr = p.building_condition ? `<div class="popup-row"><span>建築条件：</span>${{p.building_condition}}</div>` : "";
  const propNo = pid.replace(/^[^_]+_/, '');
  const srcTag = `<span class="source-tag ${{p.source==='宅建'?'source-takken':'source-reins'}}">${{p.source}}</span>`;
  const urlStr = p.url ? `<div style="margin-top:6px"><a href="${{p.url}}" target="_blank" style="color:#2980b9;font-size:0.8rem;">詳細を見る →</a></div>` : "";
  const mark = marks[pid];
  const MARK_COLORS = ['#e74c3c','#e67e22','#f1c40f','#27ae60','#2980b9','#8e44ad'];
  const colorBtns = MARK_COLORS.map(c =>
    `<div onclick="setMarkAndRefresh('${{pid}}','${{c}}')"
      style="width:24px;height:24px;border-radius:50%;background:${{c}};cursor:pointer;
             border:${{mark&&mark.color===c?'3px solid #333':'2px solid #fff'}};
             box-shadow:0 1px 3px rgba(0,0,0,0.3);flex-shrink:0"></div>`
  ).join('');
  const clearBtn = `<div onclick="setMarkAndRefresh('${{pid}}','')"
    style="width:24px;height:24px;border-radius:50%;background:#eee;border:2px dashed #aaa;
           cursor:pointer;display:flex;align-items:center;justify-content:center;
           font-size:12px;color:#999;flex-shrink:0">×</div>`;
  const markStatus = mark
    ? `<div style="margin-top:4px;font-size:0.75rem;color:#555">● <b style="color:${{mark.color}}">${{mark.label||'マーク済み'}}</b></div>`
    : '';
  const newBadge = p.is_new ? '<span class="new-badge">NEW</span>' : '';
  return `
    <div class="popup-title">${{p.icon}} ${{LABELS[p.category]||p.category}} ${{srcTag}}${{newBadge}}</div>
    <div class="popup-price" style="color:${{p.color}}">${{p.price}}</div>
    <div class="popup-row"><span>住所：</span>${{p.address}}</div>
    ${{sqmStr ? `<div class="popup-row"><span>面積：</span>${{sqmStr}}${{tsuboStr}}</div>` : ""}}
    ${{ptStr}}${{bcStr}}
    <div class="popup-row"><span>初回掲載：</span>${{p.first_seen}}</div>
    <div class="popup-propno">物件番号：${{propNo}}</div>
    ${{urlStr}}
    <div style="margin-top:8px;border-top:1px solid #eee;padding-top:6px">
      <div style="font-size:0.75rem;color:#888;margin-bottom:5px">🎨 マーク色：</div>
      <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
        ${{clearBtn}}${{colorBtns}}
      </div>
      ${{markStatus}}
    </div>
  `;
}}

function setMarkAndRefresh(pid, color) {{
  setMark(pid, color);
  // 開いているポップアップを更新
  const obj = propMarkers[pid];
  if (obj && obj.marker.isPopupOpen()) {{
    obj.marker.getPopup().setContent(buildPopupHtml(pid));
  }}
}}

const propMarkers = {{}};
PROPS.forEach((p, i) => {{
  const marker = L.marker([p.lat, p.lng], {{ icon: makeIcon(p.color, p.icon, p.is_new) }});

  // LeafletのbindPopup にfunction渡しで毎回最新HTMLを生成
  marker.bindPopup(() => buildPopupHtml(p.id), {{ maxWidth: 280 }});

  propMarkers[p.id] = {{ marker, category: p.category }};
  marker.addTo(map);
}});

// --- カラーマーク管理 ---
const MARK_KEY = 'reins_map_marks';    // {{id: {{color, label}}}}
const LABEL_KEY = 'reins_map_labels';  // {{color: label}}

let marks = JSON.parse(localStorage.getItem(MARK_KEY) || '{{}}');
let colorLabels = JSON.parse(localStorage.getItem(LABEL_KEY) || '{{}}');
let filterColor = null;  // マーク色フィルター（null=全て）

function saveMarks() {{
  localStorage.setItem(MARK_KEY, JSON.stringify(marks));
}}
function saveLabels() {{
  localStorage.setItem(LABEL_KEY, JSON.stringify(colorLabels));
}}

// カラースウォッチ選択（見た目の切り替えのみ）
function selectColor(swatch) {{
  document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('selected'));
  swatch.classList.add('selected');
}}

function setMark(id, color) {{
  if (!color) {{
    // 色なし→マーク解除
    delete marks[id];
  }} else {{
    const label = document.getElementById('color-label-input').value.trim();
    if (label) colorLabels[color] = label;
    saveLabels();
    marks[id] = {{ color: color, label: colorLabels[color] || '' }};
  }}
  saveMarks();
  updateMarkerIcons();
  updateColorFilterRow();
  renderList();
}}

function updateMarkerIcons() {{
  PROPS.forEach(p => {{
    const obj = propMarkers[p.id];
    if (!obj) return;
    const mark = marks[p.id];
    if (mark) {{
      obj.marker.setIcon(makeMarkIcon(mark.color, p.icon));
    }} else {{
      obj.marker.setIcon(makeIcon(p.color, p.icon, p.is_new));
    }}
  }});
}}

function makeMarkIcon(markColor, emoji) {{
  return L.divIcon({{
    className: "",
    html: `<div style="position:relative;width:32px;height:32px">
      <div style="background:${{markColor}};color:#fff;border-radius:50%;
        width:32px;height:32px;display:flex;align-items:center;justify-content:center;
        font-size:15px;border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.5);">
        ${{emoji}}
      </div>
      <div style="position:absolute;top:-4px;right:-4px;width:12px;height:12px;
        background:${{markColor}};border:2px solid #fff;border-radius:50%;"></div>
    </div>`,
    iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -18],
  }});
}}

function updateColorFilterRow() {{
  const usedColors = [...new Set(Object.values(marks).map(m => m.color))];
  const row = document.getElementById('color-filter-row');
  if (usedColors.length === 0) {{ row.innerHTML = ''; return; }}

  let html = '<span style="font-size:0.72rem;color:#999;margin-right:4px;">絞込:</span>';
  html += `<div class="cfbtn ${{filterColor===null?'active':''}}" onclick="setColorFilter(null)">全て</div>`;
  usedColors.forEach(c => {{
    const lbl = colorLabels[c] || '';
    html += `<div class="cfbtn ${{filterColor===c?'active':''}}" onclick="setColorFilter('${{c}}')" data-color="${{c}}">
      <span class="mark-dot" style="background:${{c}}"></span>${{lbl||'マーク'}}
    </div>`;
  }});
  row.innerHTML = html;
}}

function setColorFilter(color) {{
  filterColor = color;
  updateColorFilterRow();
  applyVisibility();
  renderList();
}}

// フィルター状態
const filterState = {{
  land: true, house: true, mansion: true,
  fav: false, new: false,
  reins: true, takken: true
}};

function applyVisibility() {{
  // propMarkers はキー=id, 値={{marker,category}} なので entries で回す
  Object.entries(propMarkers).forEach(([id, obj]) => {{
    const grpName = CAT_GROUP[obj.category];
    const src = obj.category.endsWith('_tk') ? 'takken' : 'reins';
    const p = PROP_MAP[id];
    let show = filterState[grpName] && filterState[src];
    if (filterState.fav) show = show && !!marks[id];
    if (filterState.new) show = show && !!(p && p.is_new);
    if (filterColor !== null) show = show && !!(marks[id] && marks[id].color === filterColor);
    show ? obj.marker.addTo(map) : map.removeLayer(obj.marker);
  }});
}}

function toggle(btn) {{
  const grp = btn.dataset.cat;
  filterState[grp] = !filterState[grp];
  btn.classList.toggle("on");
  applyVisibility();
  renderList();
}}

function renderList() {{
  const el = document.getElementById("list");
  const visible = PROPS.filter(p => {{
    const grp = CAT_GROUP[p.category];
    const src = p.category.endsWith('_tk') ? 'takken' : 'reins';
    if (!filterState[grp]) return false;
    if (!filterState[src]) return false;
    if (filterState.fav && !marks[p.id]) return false;
    if (filterState.new && !p.is_new) return false;
    if (filterColor !== null && (!marks[p.id] || marks[p.id].color !== filterColor)) return false;
    return true;
  }});
  el.innerHTML = visible.map(p => {{
    const tsuboDetail = p.tsubo
      ? `${{p.tsubo}}坪${{p.price_per_tsubo ? " / 坪" + p.price_per_tsubo.toLocaleString() + "円" : ""}}`
      : "";
    const srcCls = p.source === '宅建' ? 'source-takken' : 'source-reins';
    const mark = marks[p.id];
    const markDot = mark ? `<span class="mark-dot card-mark" style="background:${{mark.color}};width:12px;height:12px;border:2px solid #fff;box-shadow:0 1px 2px rgba(0,0,0,0.3)"></span>` : '';
    const newBadge = p.is_new ? '<span class="new-badge">NEW</span>' : '';
    return `<div class="card" onclick="focusProp('${{p.id}}')">
      <span class="cat-tag" style="background:${{p.color}}">${{LABELS[p.category]||p.category}}</span>
      <span class="source-tag ${{srcCls}}">${{p.source}}</span>${{newBadge}}
      ${{markDot}}
      <div class="card-price">${{p.price}}</div>
      <div class="card-addr">${{p.address||"（住所なし）"}}</div>
      ${{tsuboDetail ? `<div class="card-detail">${{tsuboDetail}}</div>` : ""}}
    </div>`;
  }}).join("");
}}

function focusProp(id) {{
  const obj = propMarkers[id];
  if (!obj) return;
  map.setView(obj.marker.getLatLng(), 16);
  obj.marker.openPopup();
}}

updateMarkerIcons();
updateColorFilterRow();
renderList();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"地図HTML生成: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = generate_map_html()
    print(f"出力: {path}")
