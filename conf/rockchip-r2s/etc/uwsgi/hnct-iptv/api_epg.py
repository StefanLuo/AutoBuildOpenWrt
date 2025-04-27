import os
import time
import requests
import re
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from functools import wraps
from flask import Flask, request, Response, abort, redirect, jsonify
from urllib.parse import urlparse, parse_qs, urlencode

app = Flask(__name__)

# ====== 配置区 ======
API_TOKEN = "LuoCan_2025x@" # 简单固定Token
TIMEOUT = 5 # 请求超时时间（秒）
CACHE_EXPIRE_SECONDS = 12 * 60 * 60 # 缓存有效期：12小时

IPTV_URL = "http://10.255.0.110/mgtv_hndx/EPGV2/GetChannelList" # IPTV频道数据接口
IPTV_EPG_URL = "http://10.255.9.200/IPTV_EPG/Channel/GetChannelsList" # EPG频道列表接口
IPTV_DLPROXY = "http://iptvhn.stefanluo.xyz:8444/iptv" # IPTV播放代理
IPTV_ZBPROXY = "http://livehn.stefanluo.xyz:8444" # 视频代理
M3U_OUTPUT_FILE = "hniptv.m3u" # M3U文件输出路径
EPG_FILE = "epg.xml" # EPG文件输出路径

# 模拟湖南电信IPTV盒子的User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 9) HN-EC6108V9/1.0 "
    "(Linux;U;Android 9) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/74.0.3729.136 Safari/537.36"
)
# ====================

def requires_auth(f):
    """装饰器：接口必须携带正确的Token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        else:
            token = request.args.get("token")
            if 'utc' in token:
                token = re.sub(r"\?.*", "", token)
        if token != API_TOKEN:
            abort(401, description="未授权")  # Token不匹配则返回401
        return f(*args, **kwargs)
    return decorated

def is_cache_valid(filepath):
    """判断文件是否存在且未过期"""
    if not os.path.exists(filepath):
        return False
    return (time.time() - os.path.getmtime(filepath)) < CACHE_EXPIRE_SECONDS

def fetch_channels(url, params):
    """获取频道列表数据，模拟IPTV盒子User-Agent"""
    try:
        resp = requests.get(
            url, params=params, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        abort(502, description=f"获取频道数据失败: {e}")

def fetch_epg(channel_id, after_day):
    """获取指定频道的EPG数据，模拟IPTV盒子User-Agent"""
    params = {
        "AfterDay": after_day,
        "BeforeDay": 0,
        "TimeZone": 8,
        "OutputType": "json",
        "Version": "YYS.4.5.19.266.2.HNDX.0.0_Release_HW_4K",
        "VideoType": 1,
        "Mode": "relative",
        "VideoId": channel_id
    }
    url = (
        "http://10.255.0.110/mgtv_hndx/BasicIndex/GetPlaybill?"
        + "&".join(f"{k}={v}" for k, v in params.items())
    )
    try:
        resp = requests.get(
            url, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        abort(502, description=f"获取EPG数据失败: {e}")

def convert_punctuation_to_fullwidth(input_str):
    """将半角标点转换为全角标点"""
    punctuation_map = {
        '.': '。', ',': '，', '?': '？', '!': '！', ':': '：', ';': '；',
        '"': '“', "'": '‘', '(': '（', ')': '）', '[': '【', ']': '】',
        '{': '｛', '}': '｝', '<': '《', '>': '》', '_': '—', '@': '＠',
        '#': '＃', '$': '＄', '%': '％', '&': '＆', '*': '＊', '+': '＋',
        '=': '＝', '/': '／', '\\': '＼', '^': '＾', '`': '｀', '~': '～'
    }
    return ''.join(punctuation_map.get(c, c) for c in input_str)

def convert_to_utc(time_str):
    """将 EPG 时间字符串转换为 UTC 时间字符串"""
    try:
        dt = datetime.strptime(time_str, "%Y%m%d%H%M%S")
        return (dt - timedelta(hours=8)).strftime("%Y%m%d%H%M%S")
    except:
        return None

def convert_timestamp_to_utc(ts):
    """将时间戳转换为 UTC 时间字符串"""
    try:
        return datetime.utcfromtimestamp(ts).strftime("%Y%m%d%H%M%S")
    except:
        return None

def generate_m3u():
    """生成 M3U 播放列表，并写入本地文件"""
    params = {
        "platform": "IPTV+",
        "includePlaybill": 0,
        "operator": 1,
        "videoType": 1,
        "version": "YYS.5.5.5.266.6.HNDXIPTV.0.0_Release_ZTE_4K",
        "includeSubData": 0,
        "sortType": "weight",
        "rootCategoryId": ""
    }
    data = fetch_channels(IPTV_EPG_URL, params)
    if data.get("result", {}).get("reason") != "ok":
        abort(502, description="IPTV 数据获取失败")

    now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    lines = ["#EXTM3U", f"# 更新: {now}"]

    for cat in data.get("categoryList", []):
        if cat["categoryId"] in ["1000202", "1000221"]:
            continue
        for ch in data.get("channelList", []):
            if cat["categoryId"] not in ch.get("categoryId", ""):
                continue
            imp = ch.get("importId"); raw = ch.get("playUrl", "")
            if not imp or not raw:
                continue
            pu = raw[6:]
            lines.append(
                f'#EXTINF:-1 tvg-id="{ch["channelName"]}" '
                f'tvg-logo="https://iptvct.stefanluo.xyz:8443/Logo/{ch["channelName"]}.png" '
                f'group-title="{cat["categoryName"]}",{ch["channelName"]}'
            )
            lines.append(
                f'{IPTV_DLPROXY}?url=http://124.232.231.172:8089/'
                f'000000002000/{imp}/index.m3u8?zte_offset=0&ispcode=2&Multicast={pu}'
            )
    content = "\n".join(lines) + "\n"
    with open(M3U_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    return content

def generate_epg_xml(after_day):
    """生成 EPG XML 数据，并写入本地文件"""
    params = {
        "OutputType": "json",
        "Version": "YYS.5.5.5.266.6.HNDXIPTV.0.0_Release_ZTE_4K",
        "CategoryId": 1000,
        "MediaAssetsId": "live"
    }
    data = fetch_channels(IPTV_URL, params)
    root = ET.Element("tv", {"generator-info-name": "湖南电信IPTV-EPG"})

    for item in data.get("l", {}).get("il", []):
        c = ET.SubElement(root, "channel", {"id": item["name"]})
        ET.SubElement(c, "display-name").text = item["name"]
        epg = fetch_epg(item["id"], after_day)
        for day in epg.get("day", []):
            for v in day.get("item", []):
                st = datetime.strptime(f"{day['day']} {v['begin']}", "%Y%m%d %H%M%S")
                et = st + timedelta(seconds=int(v["time_len"]))
                s_str, e_str = st.strftime("%Y%m%d%H%M%S"), et.strftime("%Y%m%d%H%M%S")
                p = ET.SubElement(root, "programme",
                    {"start": f"{s_str} +0800", "stop": f"{e_str} +0800", "channel": item["name"]}
                )
                ET.SubElement(p, "title").text = convert_punctuation_to_fullwidth(v["text"])
    xml_b = ET.tostring(root, encoding="utf-8", method="xml")
    with open(EPG_FILE, "wb") as f:
        f.write(xml_b)
    return xml_b
    
def update_m3u8_url(input_url: str, starttime: str, endtime: str, new_ispcode: str = '3') -> str:
    # 分割 base 与 query
    if '?' not in input_url:
        raise ValueError("输入 URL 必须包含查询串")
    base, qs = input_url.split('?', 1)

    # 解析原始参数
    params = parse_qs(qs, keep_blank_values=True)

    # 更新或新增参数
    params['ztestarttime'] = [starttime]
    params['starttime'] = [starttime]
    params['endtime'] = [endtime]
    params['zteendtime'] = [endtime]
    params['ispcode'] = [new_ispcode]

    # 重新拼接
    new_qs = urlencode(params, doseq=True)
    return f"{base}?{new_qs}"

@app.route("/iptv")
@requires_auth
def iptv_converter():
    """处理 IPTV 流的转换和重定向"""
    source_url = request.args.get("url")
    multicast = request.args.get("Multicast")
    playseek = request.args.get("playseek")
    ispcode = request.args.get("ispcode")
    token = request.args.get("token")

    if not source_url:
        abort(400, description="缺少URL参数")

    if multicast and '?utc' in multicast:
        utc = multicast.split('?', 1)[1].replace("utc=", "")
    elif '?utc' in token:
        utc = token.split('?', 1)[1].replace("utc=", "")
    else:
        utc = request.args.get("utc")

    base = f"{source_url}&ispcode={ispcode}" + (f"&Multicast={multicast}" if multicast else "")
    try:
        r1 = requests.get(base, headers={"User-Agent": USER_AGENT}, allow_redirects=False, timeout=TIMEOUT)
        r1 = re.sub(r"^https?://[^/]+", IPTV_ZBPROXY, r1.headers.get("Location"))
        r2 = requests.get(r1, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=TIMEOUT)
        real_url = r2.text.strip().splitlines()[-1]
    except Exception as e:
        abort(502, description=f"流重定向失败: {e}")

    channel_path = urlparse(base).path.rsplit('/', 1)[0] + '/'
    final = f"{IPTV_ZBPROXY}{channel_path}{real_url}"
    if playseek:
        parts = playseek.split("-")
        if len(parts) != 2:
            abort(400, description="PLAYSEEK参数错误")
        st, et = convert_to_utc(parts[0]), convert_to_utc(parts[1])
        if not st or not et:
            abort(400, description="时间格式错误")
        final = update_m3u8_url(final, st, et, '3')
    elif utc:
        st, et = convert_timestamp_to_utc(int(utc)), convert_timestamp_to_utc(int(utc)+2*60*60)
        final = update_m3u8_url(final, st, et, '3')
    
    return redirect(final)

@app.route(f"/{M3U_OUTPUT_FILE}")
@requires_auth
def get_m3u():
    """返回生成的 M3U 文件，支持缓存"""
    if is_cache_valid(M3U_OUTPUT_FILE):
        with open(M3U_OUTPUT_FILE, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="audio/x-mpegurl")
    try:
        c = generate_m3u()
        return Response(c, mimetype="audio/x-mpegurl")
    except Exception as e:
        abort(500, description=f"生成M3U失败: {e}")

@app.route(f"/{EPG_FILE}")
@requires_auth
def get_epg():
    """返回生成的 EPG XML 文件，支持缓存"""
    ad = int(request.args.get("after_day", "3"))
    if is_cache_valid(EPG_FILE):
        with open(EPG_FILE, "rb") as f:
            return Response(f.read(), mimetype="application/xml")
    try:
        xb = generate_epg_xml(ad)
        return Response(xb, mimetype="application/xml")
    except Exception as e:
        abort(500, description=f"生成EPG失败: {e}")

# 错误处理
@app.errorhandler(400)
def bad_request(e): return jsonify(error=e.description), 400
@app.errorhandler(401)
def unauthorized(e): return jsonify(error=e.description), 401
@app.errorhandler(502)
def gateway_error(e): return jsonify(error=e.description), 502
@app.errorhandler(500)
def server_error(e): return jsonify(error=e.description if hasattr(e,'description') else '内部服务器错误'), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1234, debug=True)
