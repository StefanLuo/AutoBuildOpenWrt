import os
import time
import re
import json
import logging
import queue
import asyncio
import aiofiles
import httpx
import glob
import atexit
from datetime import datetime, timedelta, timezone
from logging.handlers import QueueHandler, QueueListener
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs, urlencode, urljoin, quote, urlunparse
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, Response, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from async_lru import alru_cache
from pathlib import Path
from xml.sax.saxutils import escape

# ====== 配置区 ======
API_TOKEN = "LuoCan_2025x@"  # 简单固定Token
TIMEOUT = 10  # 请求超时时间（秒）
# 并发限制（可调整）
EPG_CONCURRENCY = 20

IPTV_URL = "http://10.255.0.110/mgtv_hndx/EPGV2/GetChannelList"  # IPTV频道数据接口
IPTV_EPG_URL = "http://10.255.9.200/IPTV_EPG/Channel/GetChannelsList"  # EPG频道列表接口
IPTV_DLPROXY = "https://iptvhn.stefanluo.xyz:8443"  # IPTV播放代理
IPTV_ZBPROXY = "https://livehn.stefanluo.xyz:8443"  # 视频代理

BASE_DIR = Path(__file__).resolve().parent

M3U_FILENAME = "hniptv.m3u"
EPG_FILENAME = "epg.xml"

M3U_OUTPUT_FILE = BASE_DIR / M3U_FILENAME  # M3U文件输出路径
EPG_FILE = BASE_DIR / EPG_FILENAME  # EPG文件输出路径

# 模拟湖南电信IPTV盒子的User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 9) HN-EC6108V9/1.0 "
    "(Linux;U;Android 9) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/74.0.3729.136 Safari/537.36"
)
# ====================

# ====== 缓存目录 & 配置 ======
EPG_CACHE_DIR = Path("/etc/hnct-iptv/epg_cache")
CHANNELS_CACHE_DIR = Path("/etc/hnct-iptv/channels_cache")
EPG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CHANNELS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CHANNELS_CACHE_FILE = CHANNELS_CACHE_DIR / "channels.json"
EPG_CHANNELS_CACHE_FILE = CHANNELS_CACHE_DIR / "epg_channels.json"
EPG_ALL_CACHE_FILE = EPG_CACHE_DIR / "epg_all.json"
EPG_MEM = None
EPG_MEM_TIME = 0
CACHE_TTL = 300  # 5分钟

def setup_logger():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    log_queue = queue.Queue(maxsize=10000)
    
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    
    queue_handler = QueueHandler(log_queue)
    
    listener = QueueListener(log_queue, stream_handler)
    listener.start()
    
    atexit.register(listener.stop)
    
    # ===== 业务 logger =====
    logger = logging.getLogger("iptv")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(queue_handler)
    logger.propagate = False
    
    # ===== root logger（防止第三方日志丢失）=====
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.handlers.clear()
    root.addHandler(queue_handler)
    
    return logger

logger = setup_logger()

# ===== 第三方日志降噪 =====
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.ERROR)

app = FastAPI()

# 允许访问的来源列表（可设置为 '*'）
# origins = [
#     "*" # 表示允许所有来源
#     # 或者指定来源：
#     # "https://example.com",
#     # "http://localhost:8080",
# ]

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=origins,             # 允许的来源
#     allow_credentials=False,           # 是否支持 cookie
#     allow_methods=["*"],               # 允许的 HTTP 方法
#     allow_headers=["*"],               # 允许的请求头
#     expose_headers=["Content-Length", "Content-Range"],  # （可选）允许前端读取的响应头
# )

# 全局中间件：为所有响应添加额外头部
# @app.middleware("http")
# async def add_global_headers(request: Request, call_next):
#     response = await call_next(request)
#     response.headers["Accept-Ranges"] = "bytes"
#     return response

# 异步 HTTP Client
#async_client = httpx.AsyncClient(timeout=TIMEOUT, limits=httpx.Limits(max_connections=50, max_keepalive_connections=50))
async_client = httpx.AsyncClient(http2=False, limits=httpx.Limits(max_keepalive_connections=0))

# ====== 权限检查 ======
async def requires_auth(request: Request):
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    else:
        token = request.query_params.get("token")
        if token and 'utc' in token:
            token = re.sub(r"\?.*", "", token)
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="未授权")
    return token

# ====== 工具：当天零点时间 & 日级缓存检查 ======
def _today_midnight_ts():
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()

def is_cache_valid_daily(filepath: str) -> bool:
    """判断文件是否存在且修改时间在当天0点之后（即缓存到当天0点）。"""
    if not os.path.exists(filepath):
        return False
    mtime = filepath.stat().st_mtime
    return mtime >= _today_midnight_ts()

def cleanup_old_cache():
    """清理过期缓存文件（前一天及更早）"""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = midnight.timestamp()
    for folder in [EPG_CACHE_DIR, CHANNELS_CACHE_DIR]:
        for path in folder.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except Exception:
                pass

# ====== 带日级缓存的 fetch_channels ======
async def fetch_channels_cached(url, params, channelType="epg"):
    """
    请求频道列表并缓存到 CHANNELS_CACHE_FILE（按天刷新）。
    若当天已经存在缓存则直接返回缓存 JSON。
    """
    channels_cache_file = EPG_CHANNELS_CACHE_FILE if channelType == "epg" else CHANNELS_CACHE_FILE
    # 如果当天缓存有效，直接返回
    if channels_cache_file.exists() and is_cache_valid_daily(channels_cache_file):
        async with aiofiles.open(channels_cache_file, "r", encoding="utf-8") as f:
            txt = await f.read()
            return json.loads(txt)
    
    # 否则请求并缓存
    data = {}
    
    CHANNELS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    resp = await async_client.get(url, params=params, headers={"User-Agent": USER_AGENT})
    data = resp.json()
    
    async with aiofiles.open(channels_cache_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False))
    
    return data

# ====== 带日级缓存的 fetch_epg ======
async def fetch_epg_cached(channel_list, after_day):
    """
    为每个频道单独缓存 EPG JSON（按天刷新）。
    并发安全通过 asyncio.Semaphore 在调用端控制。
    """
    global EPG_MEM, EPG_MEM_TIME
    
    now = time.time()
    
    # =========================
    # 1. 内存缓存（最快）
    # =========================
    if EPG_MEM and now - EPG_MEM_TIME < CACHE_TTL:
        return EPG_MEM
    
    # =========================
    # 2. 文件缓存（只读一次）
    # =========================
    if EPG_ALL_CACHE_FILE.exists() and is_cache_valid_daily(EPG_ALL_CACHE_FILE):
        async with aiofiles.open(EPG_ALL_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
            EPG_MEM = data
            EPG_MEM_TIME = now
            return data
    
    # =========================
    # 3. 没缓存 → 批量请求
    # =========================
    
    epg_all = {}

    sem = asyncio.Semaphore(EPG_CONCURRENCY)
    
    async def fetch(item):
        async with sem:
            channel_id = item["id"]
            try:
                params = {
                    "AfterDay": after_day,
                    "BeforeDay": 7,
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
                EPG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                resp = await async_client.get(url, headers={"User-Agent": USER_AGENT})
                epg_all[channel_id] = resp.json()
            except:
                epg_all[channel_id] = {}
            
    await asyncio.gather(*(fetch(i) for i in channel_list))
    
    async with aiofiles.open(EPG_ALL_CACHE_FILE, "w", encoding="utf-8") as f:
        await f.write(json.dumps(epg_all, ensure_ascii=False))
    
    EPG_MEM = epg_all
    EPG_MEM_TIME = now
    
    return epg_all

def convert_punctuation_to_fullwidth(input_str):
    """将半角标点转换为全角标点"""
    # punctuation_map = {
    #     '.': '。', ',': '，', '?': '？', '!': '！', ':': '：', ';': '；',
    #     '"': '“', "'": '‘', '(': '（', ')': '）', '[': '【', ']': '】',
    #     '{': '｛', '}': '｝', '<': '《', '>': '》', '_': '—', '@': '＠',
    #     '#': '＃', '$': '＄', '%': '％', '&': '＆', '*': '＊', '+': '＋',
    #     '=': '＝', '/': '／', '\\': '＼', '^': '＾', '`': '｀', '~': '～'
    # }
    punctuation_map = {
        '（': '(',
        '）': ')'
    }
    return ''.join(punctuation_map.get(c, c) for c in input_str)

def _get_local_offset_timedelta():
    """返回当前系统本地时区相对于 UTC 的偏移。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return now.utcoffset() or timezone.utc.utcoffset(now)

def normalize_to_utc(time_str, input_is_utc=True, debug=True):
    """
    将输入时间字符串（YYYYMMDDHHMMSS）转换为 UTC 时间戳（秒）。
    默认假设输入是本地时间，如果 input_is_utc=True，则按 UTC 处理。
    """
    
    if not time_str:
        logger.error("回看时间为空")
    
    try:
        naive = datetime.strptime(time_str, "%Y%m%d%H%M%S")
    except ValueError:
        logger.error("时间格式错误：%s", time_str)
    
    if input_is_utc:
        dt_utc = naive.replace(tzinfo=timezone.utc)
        source = "UTC"
    else:
        tz_local = ZoneInfo("Asia/Shanghai")
        dt_local = naive.replace(tzinfo=tz_local)
        dt_utc = dt_local.astimezone(timezone.utc)
        source = "LOCAL"
    
    if debug:
        logger.info("输入 %s 被识别为 %s -> UTC %s", time_str, source, dt_utc.isoformat())
    
    return int(dt_utc.strftime("%Y%m%d%H%M%S"))

def convert_timestamp_to_utc(ts):
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    except:
        return None

# ====== URL 重写 ======
@alru_cache(maxsize=1000)
async def resolve_real_url(base: str) -> str:
    try:
        # 注意：get 返回 Response 对象，await 它即可
        r = await async_client.get(base, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("Location")
            if location:
                # 递归解析多级重定向
                return await resolve_real_url(location)
        return str(r.url)
    except Exception as e:
        logger.info("URL %s 已无重定向", base)
        return base

def rewrite_line(line, base_url):
    try:
        if line.startswith("#") or not line.strip():
            return line
        full_url = urljoin(base_url, line.strip())
        return f"/sub_playlist?url={quote(full_url, safe='')}"
    except Exception as e:
        logger.error("处理出错：%s", e)
        return line

def rewrite_line_sub(line, base_url):
    line = line.strip()
    if line.startswith('#') or line == '':
        return line
    full_url = urljoin(base_url, line)
    parsed_url = urlparse(full_url)
    base_path = parsed_url.path
    query_params = parse_qs(parsed_url.query)
    encoded_query = urlencode(query_params, doseq=True)
    return f"/ts_proxy?url={quote(urlunparse(parsed_url._replace(path=base_path, query=encoded_query)), safe='')}"

def update_m3u8_url(input_url: str, starttime: str, endtime: str, new_ispcode: str = '3') -> str:
    base, qs = input_url.split('?', 1)
    params = parse_qs(qs, keep_blank_values=True)
    params['ztestarttime'] = [starttime]
    params['starttime'] = [starttime]
    params['endtime'] = [endtime]
    params['zteendtime'] = [endtime]
    params['ispcode'] = [new_ispcode]
    new_qs = urlencode(params, doseq=True)
    return f"{base}?{new_qs}"
    
# ====== 并发 + 日级缓存的 generate_m3u ======
async def generate_m3u():
    """
    生成 M3U 并缓存到 hniptv.m3u（按天刷新）。
    先使用 fetch_channels_cached 获取频道数据（可复用缓存），然后构建 M3U 文本并异步写入。
    """
    # 如果 hniptv.m3u 当天已缓存，直接返回
    if os.path.exists(M3U_OUTPUT_FILE) and os.path.getmtime(M3U_OUTPUT_FILE) >= _today_midnight_ts():
        async with aiofiles.open(M3U_OUTPUT_FILE, "r", encoding="utf-8") as f:
            return await f.read()
    
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
        data = await fetch_channels_cached(IPTV_EPG_URL, params, "channel")
        now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        lines = ["#EXTM3U", f"#更新：{now}"]
        
        for cat in data.get("categoryList", []):
            for ch in data.get("channelList", []):
                if cat["categoryId"] not in ch.get("categoryId", ""):
                    continue
                imp = ch.get("importId"); raw = ch.get("playUrl", "")
                if not imp or not raw:
                    continue
                pu = raw[6:]
                lines.append(
                    f'#EXTINF:-1 tvg-id="{ch["channelName"]}" '
                    f'tvg-logo="https://iptvct.speedtest.stefanluo.xyz:8443/Logo/{ch["channelName"]}.png" '
                    f'group-title="{cat["categoryName"]}",{ch["channelName"]}'
                )
                lines.append(
                    f'{IPTV_DLPROXY}/iptv?url=http://124.232.231.172:8089/'
                    f'000000002000/{imp}/index.m3u8?starttime=&zte_offset=0&ispcode=2&Multicast={pu}&token={API_TOKEN}'
                )
        
        content = "\n".join(lines) + "\n"
        
        async with aiofiles.open(M3U_OUTPUT_FILE, "w", encoding="utf-8") as f:
            await f.write(content)
    except Exception as e:
        logger.error("调用 generate_m3u 方法报错：%s", e)
    
    return content

def calc_stop_fast(start, duration):
    hh = int(start[8:10])
    mm = int(start[10:12])
    ss = int(start[12:14])

    total = hh * 3600 + mm * 60 + ss + int(duration)

    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60

    return (f"{start[:8]}{hh:02d}{mm:02d}{ss:02d}")

#def build_epg_xml(pairs):
#    parts = []
#    append = parts.append
#    
#    append('<?xml version="1.0" encoding="UTF-8"?>')
#    append('<tv generator-info-name="湖南电信 IPTV-EPG">')
#    
#    # =====================
#    # CHANNELS
#    # =====================
#    for item, _ in pairs:
#        name = item["name"]
#        n = escape(name)
#        
#        append(
#            f'<channel id="{n}">'
#            f'<display-name>{n}</display-name>'
#            '</channel>'
#        )
#    
#    # =====================
#    # PROGRAMMES
#    # =====================
#    for item, epg in pairs:
#        name = escape(item["name"])
#        #if name == "CCTV1高清":
#            #with open(f"{EPG_CACHE_DIR}/{name}_v-title.json", "a", encoding="utf-8") as f:
#        days = epg.get("day", [])
#        if not days:
#            continue
#        for day in days:
#            d = day.get("day")
#            if not d:
#                continue
#            d = str(d)
#            #y, m, dd = d[0:4], d[4:6], d[6:8]
#            #f.write(json.dumps(day, ensure_ascii=False, indent=2))
#            #f.write(f"{y}、{m}、{dd}\n")
#            items = day.get("item", [])
#            if not items:
#                continue
#            for v in items:
#                b = v["begin"]
#                if not b:
#                    continue
#                tl = v["time_len"]
#                if not tl:
#                    continue
#                start_str = f"{d}{b}"
#                start_dt = datetime.strptime(start_str, "%Y%m%d%H%M%S")
#                stop_dt = (start_dt + timedelta(seconds=int(tl or 0))).strftime("%Y%m%d%H%M%S")
#                stop_str = str(stop_dt)
#                title = escape(v.get("text", ""))
#                #f.write(f"{start}:{title}\n")
#                
#                append(
#                    f'<programme start="{start_str} +0800" stop="{stop_str} +0800" channel="{name}">'
#                    f'<title>{title}</title>'
#                    '</programme>'
#                )
#    append('</tv>')
#    
#    return "\n".join(parts).encode("utf-8")

def build_epg_xml(pairs):
    esc = escape
    
    with open(EPG_FILE, "w", encoding="utf-8", buffering=1024 * 1024) as f:
        w = f.write
        
        w('<?xml version="1.0" encoding="UTF-8"?>\n')
        w('<tv generator-info-name="湖南电信 IPTV-EPG">\n')
    
        # =====================
        # CHANNELS
        # =====================
        for item, _ in pairs:
            channel_name = escape(item["name"])
            
            w(f'<channel id="{channel_name}">\n<display-name>{channel_name}</display-name>\n</channel>\n')
        
        # =====================
        # PROGRAMMES
        # =====================
        for item, epg in pairs:
            channel_name = escape(item["name"])
            # if name == "CCTV1高清":
                # with open(f"{EPG_CACHE_DIR}/{name}_v-title.json", "a", encoding="utf-8") as f:
            days = epg.get("day", [])
            for day in days:
                d = day.get("day")
                if not d:
                    continue
                # y, m, dd = d[0:4], d[4:6], d[6:8]
                # f.write(json.dumps(day, ensure_ascii=False, indent=2))
                # f.write(f"{y}、{m}、{dd}\n")
                items = day.get("item", [])
                for v in items:
                    b = v["begin"]
                    if not b:
                        continue
                    tl = v.get("time_len", 0)
                    start = f"{d}{b}"
                    stop = calc_stop_fast(start, tl)
                    title = escape(convert_punctuation_to_fullwidth(v["text"]))
                    # f.write(f"{start}:{title}\n")
                    
                    w(f'<programme channel="{channel_name}" start="{start} +0800" stop="{stop} +0800">\n<title>{title}</title></programme>\n')
        w('</tv>')
        f.flush()
        os.fsync(f.fileno())

# ====== 并发 + 日级缓存的 generate_epg_xml ======
async def generate_epg_xml(after_day: int = 3):
    """
    并发获取每个频道的 EPG（每个频道结果按天缓存到 epg_cache/），
    最后合并成 epg.xml 并以异步方式写入 EPG_FILE。
    缓存策略：生成的 epg.xml 也按天缓存（当天内二次请求直接返回文件）。
    """
    # 如果 epg.xml 当天已缓存，直接返回其 bytes（调用方也会在 route 层检查）
    if os.path.exists(EPG_FILE) and is_cache_valid_daily(EPG_FILE):
        async with aiofiles.open(EPG_FILE, "rb") as f:
            return await f.read()
    
    # 1) 获取频道列表（支持缓存）
    params = {
        "OutputType": "json",
        "Version": "YYS.5.5.5.266.6.HNDXIPTV.0.0_Release_ZTE_4K",
        "CategoryId": 1000,
        "MediaAssetsId": "live"
    }
    
    t0 = time.perf_counter()
    
    # =====================
    # 1. 频道
    # =====================
    data = await fetch_channels_cached(IPTV_URL, params)
    channel_list = data.get("l", {}).get("il", [])
    if not channel_list:
        logger.warning("IPTV EPG 数据为空或获取失败")
    
    # =====================
    # 2. EPG 缓存
    # =====================
    epg_map = await fetch_epg_cached(channel_list, after_day)
    
    pairs = [
        (item, epg_map.get(item["id"], {}))
        for item in channel_list
    ]
    
    t1 = time.perf_counter()
    
    # =====================
    # 3. XML 生成
    # =====================
    build_epg_xml(pairs)
    
    t2 = time.perf_counter()
    
    # =====================
    # 4. 写文件
    # =====================
    # async with aiofiles.open(EPG_FILE, "wb") as f:
    #     await f.write(xml_bytes)
    
    t3 = time.perf_counter()
    
    logger.info("EPG缓存耗时: %.3fs", t1 - t0)
    logger.info("XML生成耗时: %.3fs", t2 - t1)
    # logger.info("写文件耗时: %.3fs", t3 - t2)
    
    # return xml_bytes

async def stream_proxy(url: str, request: Request) -> StreamingResponse:
    """
    异步流式转发代理，用于 IPTV TS 文件或其他视频流的透传。
    支持：
      - 自动处理 302/301 重定向
      - Range 断点续传
      - 首片段缓冲，减少卡顿
      - 上游报错容错，不影响播放
    """
    headers = {"User-Agent": USER_AGENT}

    # 如果客户端带了 Range 请求头，则透传
    if range_header := request.headers.get("range"):
        headers["Range"] = range_header
    
    url_lower = url.lower()
    if url_lower.endswith(".ts"):
        content_type = "video/MP2T"
    elif url_lower.endswith(".m3u8"):
        content_type = "application/vnd.apple.mpegurl"
    else:
        content_type = "video/MP2T"
    
    async def iter_stream():
        try:
            async with async_client.stream("GET", url, headers=headers, follow_redirects=True, timeout=TIMEOUT) as r:
                if r.status_code not in (200, 206):
                    logger.warning("上游错误: %s %s", r.status_code, url)
                    yield b""  # 返回空数据，不影响流
                    return
                
                async for chunk in r.aiter_bytes():
                    yield chunk
        except Exception as e:
            logger.error("上游请求异常：%s %s", url, e)
            yield b""
    
    return StreamingResponse(
        iter_stream(),
        media_type=content_type,
        headers={"Accept-Ranges": "bytes"}
    )

# ====== FastAPI 路由 ======
@app.get("/sub_playlist")
async def sub_playlist(url: str):
    try:
        final_url = await resolve_real_url(url)
        r = await async_client.get(final_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            logger.warning("子链接请求失败：%s 状态码：", final_url, r.status_code)
            content = ""
        else:
            base_url = url.rsplit("?", 1)[0]
            content = "\n".join(rewrite_line_sub(line, base_url) for line in r.text.splitlines())
        return Response(content, media_type="application/vnd.apple.mpegurl")
    except Exception as e:
        logger.error("子链接异常：%s %s", url, e)
        return Response("", media_type="application/vnd.apple.mpegurl")

@app.get("/ts_proxy")
async def ts_proxy(request: Request, url: str):
    try:
        return await stream_proxy(url, request)
    except Exception as e:
        logger.error("/ts_proxy 异常：%s %s", url, e)
        # 返回空响应，避免播放器卡顿
        async def empty_gen():
            yield b""
        return StreamingResponse(empty_gen(), media_type="video/MP2T", headers={"Accept-Ranges": "bytes"})

@app.get("/iptv")
async def iptv_converter(request: Request, 
    url: str, Multicast: str = None, playseek: str = None, tz: str = None, ispcode: str = None,
    zte_offset: str = None, token: str = None, utc: str = None,
    auth: str = Depends(requires_auth)
):
    """处理 IPTV 流的转换和重定向"""
    try:
        if Multicast and '?utc' in Multicast:
            utc = Multicast.split('?', 1)[1].replace("utc=", "")
        elif token and '?utc' in token:
            utc = token.split('?', 1)[1].replace("utc=", "")
        final_url = f"{url}&zte_offset={zte_offset}&ispcode={ispcode}" + (f"&Multicast={Multicast}" if Multicast else "")
        if playseek:
            parts = playseek.split("-")
            if len(parts) == 2:
                logger.info("回看方式 PLAYSEEK\n回看开始时间：%s，回看结束时间：%s", parts[0], parts[1])
                st = normalize_to_utc(parts[0], input_is_utc=(tz!="local"))
                et = normalize_to_utc(parts[1], input_is_utc=(tz!="local"))
                logger.info("转换后回看开始时间：%s，转换后回看结束时间：%s", st, et)
                final_url = update_m3u8_url(final_url, st, et, '3')
        elif utc:
            st, et = convert_timestamp_to_utc(int(utc)), convert_timestamp_to_utc(int(utc)+2*60*60)
            logger.info("回看方式 UTC\n回看开始时间：%s，回看结束时间：%s", st, et)
            final_url = update_m3u8_url(final_url, st, et, '3')
        final_url = await resolve_real_url(final_url)
        r = await async_client.get(final_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            logger.warning("M3U8 请求失败：%s 状态码：%s", final_url, r.status_code)
            return Response("", media_type="application/vnd.apple.mpegurl")
        ct = r.headers.get("Content-Type", "")
        if "mpegurl" in ct.lower() or url.endswith(".m3u8"):
            content = "\n".join(rewrite_line(line, final_url) for line in r.text.splitlines())
            return Response(content, media_type="application/vnd.apple.mpegurl")
        else:
            return await stream_proxy(url, request)
    except Exception as e:
        logger.error("IPTV 转换异常：%s %s", url, e)
        return Response("", media_type="application/vnd.apple.mpegurl")

@app.get("/iptv2")
async def iptv_redirect(
    url: str,
    Multicast: str = None,
    playseek: str = None,
    tz: str = None,
    ispcode: str = None,
    zte_offset: str = None,
    token: str = None,
    utc: str = None,
    auth: str = Depends(requires_auth)
):
    """
    IPTV2路由：直接返回最终播放URL的重定向，不做代理。
    """
    try:
        # 处理 Multicast 或 token 中的 utc
        if Multicast and '?utc' in Multicast:
            utc = Multicast.split('?', 1)[1].replace("utc=", "")
        elif token and '?utc' in token:
            utc = token.split('?', 1)[1].replace("utc=", "")

        final_url = f"{url}&zte_offset={zte_offset}&ispcode={ispcode}" + (f"&Multicast={Multicast}" if Multicast else "")

        if playseek:
            parts = playseek.split("-")
            if len(parts) == 2:
                logger.info("回看方式 PLAYSEEK\n回看开始时间：%s，回看结束时间：%s", parts[0], parts[1])
                st = normalize_to_utc(parts[0], input_is_utc=(tz!="local"))
                et = normalize_to_utc(parts[1], input_is_utc=(tz!="local"))
                final_url = update_m3u8_url(final_url, st, et, '3')
        elif utc:
            st, et = convert_timestamp_to_utc(int(utc)), convert_timestamp_to_utc(int(utc) + 2*60*60)
            logger.info("回看方式 UTC\n回看开始时间：%s，回看结束时间：%s", st, et)
            final_url = update_m3u8_url(final_url, st, et, '3')
        # 解析最终 URL（跟 iptv 一样处理重定向，但不代理内容）
        final_url = await resolve_real_url(final_url)
        # 返回 307 临时重定向
        return RedirectResponse(final_url, status_code=307)
    except Exception as e:
        logger.error("上游请求异常：%s %s", url, e)

async def schedule_daily_channel_epg_preload():
    """每天 0:01 预热 EPG 缓存"""
    while True:
        now = datetime.now()
        # 计算今天 0:01 的时间
        target = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)
        try:
            logger.info("开始预热缓存：%s", datetime.now())
            await generate_m3u()
            await generate_epg_xml(after_day=3)
            logger.info("完成预热缓存：%s", datetime.now())
        except Exception as e:
            logger.error("预热出错：%s", e)
        # 循环，继续等待下一天 0:01

@app.get(f"/{M3U_FILENAME}")
async def get_m3u(auth: str = Depends(requires_auth)):
    # 使用日级缓存检查
    if is_cache_valid_daily(M3U_OUTPUT_FILE):
        # 直接异步读取并返回
        async with aiofiles.open(M3U_OUTPUT_FILE, "r", encoding="utf-8") as f:
            txt = await f.read()
            return Response(txt, media_type="audio/x-mpegurl")
    try:
        content = await generate_m3u()
        return Response(content, media_type="audio/x-mpegurl")
    except Exception as e:
        logger.error("生成 M3U 失败：%s", e)

@app.get(f"/{EPG_FILENAME}")
async def get_epg(after_day: int = 3, auth: str = Depends(requires_auth)):
    # 日级缓存检查
    if is_cache_valid_daily(EPG_FILE):
        async with aiofiles.open(EPG_FILE, "rb") as f:
            return Response(await f.read(), media_type="application/xml")
    try:
        await generate_epg_xml(after_day)
        async with aiofiles.open(EPG_FILE, "rb") as f:
            return Response(await f.read(), media_type="application/xml")
    except Exception as e:
        logger.error("生成 EPG 失败：%s", e)

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(schedule_daily_channel_epg_preload())