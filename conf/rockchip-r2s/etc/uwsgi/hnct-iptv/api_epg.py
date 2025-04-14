import os
import requests
import re
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from flask import Flask, request, Response, abort, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
# 获取电信iptv必要信息接口
IPTV_URL = "http://10.255.0.110/mgtv_hndx/EPGV2/GetChannelList"
IPTV_EPG_URL = "http://10.255.9.200/IPTV_EPG/Channel/GetChannelsList"

# 你运行这个脚本的IP以及端口号
IPTV_DLPROXY = "http://iptvhn.stefanluo.xyz:8444/iptv"
IPTV_ZBPROXY = "http://livehn.stefanluo.xyz:8444"

# 文件名称
M3U_OUTPUT_FILE = "hniptv.m3u"
EPG_FILE = "epg.xml"

# 获取 IPTV 数据
def fetch_channels(url,params):
    response = requests.get(url, params=params)
    return response.json()

# 生成 M3U 文件
def generate_m3u():
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
    try:
        iptv_data = fetch_channels(url=IPTV_EPG_URL ,params=params)
        if iptv_data.get("result", {}).get("reason") != "ok":
            raise ValueError("IPTV 数据获取失败")

        now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        m3u_content = f"#EXTM3U\n# 更新: {now}\n\n"

        category_list = iptv_data.get("categoryList", [])
        channel_list = iptv_data.get("channelList", [])

        for category in category_list:
            if category["categoryId"] in ["1000202", "1000221"]:
                continue

            for channel in channel_list:
                if not channel["importId"] and not play_url:
                    continue
                if category["categoryId"] in channel.get("categoryId", ""):
                    play_url = channel.get("playUrl", "")[6:]  # 去掉前缀
                    m3u_content += (
                        # f'#EXTINF:-1 tvg-id="{channel["channelNumber"]}" '
                        f'#EXTINF:-1 tvg-id="{channel["channelName"]}" '
                        # f'tvg-logo="{channel.get("callsign", "")}",group-title="{category["categoryName"]}" '
                        f'tvg-logo="https://iptvct.stefanluo.xyz:8443/Logo/{channel["channelName"]}.png" group-title="{category["categoryName"]}",'
                        f'{channel["channelName"]}\n'
                        # f'{IPTV_ZBPROXY}?url=http://124.232.231.172:8089/000000002000/{channel["importId"]}/index.m3u8?zte_offset=0&ispcode=2&Multicast={play_url}\n'
                        f'{IPTV_DLPROXY}?url=http://124.232.231.172:8089/000000002000/{channel["importId"]}/index.m3u8?zte_offset=0&ispcode=2&Multicast={play_url}\n'
                        # f'http://124.232.231.172:8089/000000002000/{channel["importId"]}/index.m3u8?zte_offset=0&ispcode=2&Multicast={play_url}\n'
                    )

        with open(M3U_OUTPUT_FILE, "w", encoding="utf-8") as m3u_file:
            m3u_file.write(m3u_content)
    except Exception as e:
        print(f"生成 M3U 失败: {e}")

# 检查并更新 M3U 文件
def check_and_update_m3u(fi:str, after_day = 3):
    if os.path.exists(fi):
        last_modified = datetime.fromtimestamp(os.path.getmtime(fi)).strftime("%Y-%m-%d")
        if last_modified != datetime.now().strftime("%Y-%m-%d"):
            if fi == M3U_OUTPUT_FILE:
                generate_m3u()
            else:
                generate_epg_xml(after_day)
    else:
        if fi == M3U_OUTPUT_FILE:
            generate_m3u()
        else:
            generate_epg_xml(after_day)

# 获取节目单
def fetch_epg(channel_id, after_day):
    params = {"AfterDay": after_day, "BeforeDay": 0, "TimeZone": 8, "OutputType": "json",
              "Version": "YYS.4.5.19.266.2.HNDX.0.0_Release_HW_4K", "VideoType": 1, "Mode": "relative","VideoId": channel_id}
    url = f"http://10.255.0.110/mgtv_hndx/BasicIndex/GetPlaybill?" + "&".join(f"{k}={v}" for k, v in params.items())
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

# 处理节目表中特殊字符
def convert_punctuation_to_fullwidth(input_str):
    punctuation_map = {
        '.': '。',
        ',': '，',
        '?': '？',
        '!': '！',
        ':': '：',
        ';': '；',
        '"': '“',
        "'": '‘',
        '(': '（',
        ')': '）',
        '[': '【',
        ']': '】',
        '{': '｛',
        '}': '｝',
        '<': '《',
        '>': '》',
        # '-': '－',
        '_': '—',
        '@': '＠',
        '#': '＃',
        '$': '＄',
        '%': '％',
        '&': '＆',
        '*': '＊',
        '+': '＋',
        '=': '＝',
        '/': '／',
        '\\': '＼',
        '^': '＾',
        '`': '｀',
        '~': '～'
    }
    return ''.join(punctuation_map.get(char, char) for char in input_str)

# 生成 EPG XML
def generate_epg_xml(after_day):
    params = {
        "OutputType": "json",
        "Version": "YYS.5.5.5.266.6.HNDXIPTV.0.0_Release_ZTE_4K",
        "CategoryId": 1000,
        "MediaAssetsId": "live"
    }
    data = fetch_channels(url=IPTV_URL, params=params)
    xml_root = ET.Element("tv", {"generator-info-name": "湖南电信IPTV-EPG"})

    for item in data["l"]["il"]:
        channel = ET.SubElement(xml_root, "channel", {"id": item["name"]})
        ET.SubElement(channel, "display-name").text = item["name"]

        epg_data = fetch_epg(item["id"], after_day)
        for day in epg_data["day"]:
            for v in day["item"]:
                # 解析开始时间
                start_time = datetime.strptime(f"{day['day']} {v['begin']}", "%Y%m%d %H%M%S")
                # 计算结束时间
                stop_time = start_time + timedelta(seconds=int(v["time_len"]))

                # 格式化时间
                start_str = start_time.strftime("%Y%m%d%H%M%S")
                stop_str = stop_time.strftime("%Y%m%d%H%M%S")

                # 生成 EPG 结构
                programme = ET.SubElement(xml_root, "programme", {
                    "start": f"{start_str} +0800",
                    "stop": f"{stop_str} +0800",
                    "channel": item["name"]
                })
                ET.SubElement(programme, "title").text = convert_punctuation_to_fullwidth(v["text"])

    with open(EPG_FILE, "wb") as f:
        f.write(ET.tostring(xml_root, encoding="utf-8", method="xml"))

    return {"message": "EPG 文件已保存"}, 200

# 时间转换成 UTC 时区
def time_to_zone(m_time):
    try:
        dt = datetime.strptime(m_time, "%Y%m%d%H%M%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y%m%d%H%M%S")
    except ValueError:
        return None

def convert_to_utc(m_time):
    try:
        # 解析 EPG 时间字符串
        epg_time = datetime.strptime(m_time, "%Y%m%d%H%M%S")
        # 创建一个时区偏移量（UTC+8）
        time_offset = timedelta(hours=8)
        # 转换为 UTC 时间（减去 8 小时）
        utc_time = epg_time - time_offset
        return utc_time.strftime("%Y%m%d%H%M%S")
    except ValueError:
        return None
        
def generate_random_number(digits):
    if digits <= 0:
        abort(400, "随机数位数错误")
    min_value = 10**(digits - 1)
    max_value = 10**digits - 1
    return random.randint(min_value, max_value)

@app.route("/iptv")
def iptv_converter():
    source_url = request.args.get("url")
    multicast = request.args.get("Multicast")
    playseek = request.args.get("playseek")
    ispcode = request.args.get("ispcode")

    if not source_url:
        abort(404, "URL 参数缺失")

    # 获取当前时间并转换为 UTC，生成 IASHttpSessionId
    # current_time = time_to_zone(datetime.now().strftime("%Y%m%d%H%M%S"))

    # if not current_time:
        # abort(500, "时间转换失败")

    #IASHttpSessionId = f"IASHttpSessionId=RR6450{current_time}897575"

    # 解析 URL 基础地址
    url = source_url.split("?")[0]
    # 直播逻辑
    url = source_url + f'&ispcode={ispcode}'
    if multicast:
        # abort(400, "Multicast 地址缺失")
        url = url + f'&Multicast={multicast}'
    headers = {
        "User-Agent": "okhttp"
    }
    url = requests.get(url, headers=headers, allow_redirects=False).headers.get("Location")
    # print(url)
    url = requests.get(url, headers=headers, allow_redirects=False).headers.get("Location")
    # print(url)
    # http://220.170.28.10:6410/000000002000/201500000638/index.m3u8?zte_offset=0&ispcode=2&Multicast=239.76.253.246:9000&IASHttpSessionId=RR727420250410083425880469
    # if "201500000638" in url:
        # url = re.sub(r"^https?://[^/]+", IPTV_ZBBJWS4KPROXY, url)
    #else:
    url = re.sub(r"^https?://[^/]+", IPTV_ZBPROXY, url)
    if playseek:
        # 回看逻辑
        time_arr = playseek.split("-")

        if len(time_arr) != 2:
            abort(400, "playseek 参数错误")

        starttime = convert_to_utc(time_arr[0])
        endtime = convert_to_utc(time_arr[1])

        if not starttime or not endtime:
            abort(400, "时间格式错误")
        
        url = url.replace("zte_offset=0&", f"starttime={starttime}&").replace("ispcode=2&", f"endtime={endtime}&")
    print(url)
    return redirect(url)

# 提供 M3U 文件的 HTTP 接口
@app.route(f"/{M3U_OUTPUT_FILE}", methods=["GET"])
def get_m3u():
    check_and_update_m3u(M3U_OUTPUT_FILE)
    if os.path.exists(M3U_OUTPUT_FILE):
        with open(M3U_OUTPUT_FILE, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="audio/x-mpegurl")
    return Response("M3U 文件生成失败", status=500)

@app.route(f"/{EPG_FILE}", methods=["GET"])
def epg():
    after_day = int(request.args.get("after_day", "3"))
    check_and_update_m3u(EPG_FILE, after_day=after_day)

    if os.path.exists(EPG_FILE):
        with open(EPG_FILE, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1234, debug=True)
    # app.run()
