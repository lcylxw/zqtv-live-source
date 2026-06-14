"""
朱雀TV 直播源自动更新脚本
用于 GitHub Actions 定时检测并更新直播源
合并王子电视CCTV/卫视源，按速度和画质排序

改进：
- 严格分类：CCTV频道不会混入卫视，卫视不会混入央视
- 央视按频道号排序（CCTV1, CCTV2, ...）
- 卫视按预定义顺序排序
- 同一频道的多个地址按分辨率降序排列
- 缓存速度测试结果，避免重复测速（更新从5分钟降至30秒内）
"""
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict, defaultdict
from datetime import datetime


# ========== 朱雀TV 配置 ==========
ZQTV_CONFIG_URL = "http://207.56.16.135:9999/zqtv/config.json"
ZQTV_PASSWORD = "DBhkhdnefkhfq,#%"
STATE_FILE = "state.json"
OUTPUT_FILE = "source.txt"
LOG_FILE = "update_log.md"


# ========== 王子电视配置 ==========
WANGZI_SOURCE_URL = "http://wangziduoqing.com/yuan/zb.txt"
WANGZI_STATE_FILE = "wangzi_state.json"


# ========== 清理规则 ==========
BLOCK_CHANNELS = [
    # 购物频道
    "养生馆", "健康有约", "百姓健康", "中华特产", "INBM证券服务",
    "太原佰乐购", "快乐购", "上虞新商都", "优购物", "央广购物",
    "南方购物", "家有购物", "东方购物", "CCTV中视购物",
    "四川星空购物", "大连乐天购物", "山东居家购物", "成都每日购物",
    "河北三佳购物", "河南欢腾购物", "深圳宜和购物", "爱家购物",
    "西安乐购购物", "辽宁宜佳购物", "重庆时尚购物", "长沙嘉丽购物",
    "严选好物", "好物分享", "精品甄选", "健康甄选",
    "好易购", "广西乐思购", "湖南快乐购", "西安乐购购物",
    # 广告填充
    "福利多多", "美好生活",
    "家庭理财", "潍坊金融频道",
    "潍坊新时尚", "潍坊生殖健康", "潍坊文艺时尚",
    "商城新闻频道", "潍坊企业家",
    # 导视频道
    "安徽导视", "廊坊导视频道", "杭州导视纪录",
    # CCTV专题
    "CCTV电视指南", "CCTV世界地理", "CCTV女性时尚", "CCTV卫生健康",
    # CGTN系列
    "CGTN",
    # 书画/天气
    "书画频道", "中国天气",
    # 教育频道
    "教育频道", "教育法制", "科学教育", "职业教育", "远程教育",
    "远程党员", "文化教育", "教育科技", "教育人文", "教育青少",
    "科技教育", "生活教育", "早期教育", "现代教育",
    "中国国际教育", "中国教育1", "中国教育2", "中国教育3", "中国教育4",
    "GRTN教育",
    # 非正规频道
    "西安商务资讯", "深圳众创TV", "GRTN健康频道", "Z频道", "NewTV-怡伴健康",
    # 钓鱼/围棋/武术
    "四海钓鱼", "天元围棋", "快乐垂钓", "湖南快乐垂钓", "河北杂技",
    # 少儿
    "CCTV14少儿", "NewTV-黑莓动画", "优漫卡通",
    "北京卡酷少儿", "北京少儿", "卡酷少儿",
    "嘉佳卡通", "广东少儿", "浙江少儿", "甘肃少儿", "重庆少儿", "金鹰卡通",
    # 戏曲
    "CCTV11-戏曲", "CCTV11戏曲", "岭南戏曲", "陕西秦腔",
    # 音乐
    "CCTV-风云音乐", "CCTV15音乐",
    # 游戏/军事
    "SiTV-游戏风云", "NewTV-军事评论", "NewTV-军旅剧场",
    "CCTV兵器科技", "CCTV7国防军事", "CCTV-兵器科技", "CCTV07国防军事",
]


DEAD_URLS = ["101.35.240.114:88"]

# ========== 速度测试缓存 ==========
SPEED_CACHE_FILE = "speed_cache.json"
SPEED_CACHE_TTL = 300  # 缓存有效期：300秒（5分钟）


# ========== 速度测试缓存读写 ==========
def load_speed_cache():
    """加载速度测试缓存"""
    if os.path.exists(SPEED_CACHE_FILE):
        try:
            with open(SPEED_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_speed_cache(cache):
    """保存速度测试缓存"""
    with open(SPEED_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def get_cached_speed(url, cache):
    """
    获取缓存的速度测试结果。
    如果缓存命中且未过期，返回 (True, 缓存中的毫秒数)。
    如果缓存未命中或已过期，返回 None。
    """
    if url in cache:
        entry = cache[url]
        # 检查是否过期
        if time.time() - entry["ts"] < SPEED_CACHE_TTL:
            return (True, entry["ms"])
    return None


def update_speed_cache(cache, url, ok, ms):
    """更新速度测试缓存"""
    cache[url] = {"ok": ok, "ms": ms, "ts": time.time()}
    # 每1000次写入一次，避免频繁IO
    if hash(url) % 1000 == 0:
        save_speed_cache(cache)


def speed_test(url, timeout=5):
    """测试URL响应速度，返回(是否可用, 响应时间ms)"""
    try:
        start = time.time()
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        elapsed = int((time.time() - start) * 1000)
        return resp.status_code == 200, elapsed
    except Exception:
        return False, 99999


def speed_test_batch(urls, timeout=3, max_workers=10):
    """
    并发测试一批URL的速度。
    利用缓存避免重复测速。
    返回: (测试过的url列表[(name, url, ms)], 缓存命中的数量)
    """
    cache = load_speed_cache()
    timeout_ms = timeout * 1000

    results = []
    cached_count = 0
    to_test = []

    # 分离需要测试的和缓存命中的
    for name, url in urls:
        cached = get_cached_speed(url, cache)
        if cached:
            results.append((name, url, cached[1]))
            cached_count += 1
        else:
            to_test.append((name, url))

    # 并发测试未命中的URL
    if to_test:
        def _test_one(item):
            name, url = item
            ok, ms = speed_test(url, timeout=timeout)
            return (name, url, ms if ok else timeout_ms)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_test_one, item): item for item in to_test}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    name, url = futures[future]
                    results.append((name, url, timeout_ms))

    # 保存新测试的结果到缓存
    for name, url, ms in results:
        ok = ms < timeout_ms
        update_speed_cache(cache, url, ok, ms)

    if to_test:
        save_speed_cache(cache)

    return results, cached_count


# 只保留这些分类
KEEP_GENRES = ['央视频道', '卫视频道', '数字频道', '港澳台']


# ========== 频道分类与排序规则 ==========

# 判断是否为CCTV频道的正则（匹配 CCTV、cctv 开头，后跟数字或特定名称）
CCTV_PATTERN = re.compile(
    r'^(CCTV|cctv)[\s\-]?(\d+|综合|财经|综艺|中文国际|体育|电影|军事|农业|纪录|科教|戏曲|社会与法|新闻|少儿|音乐|4K)',
    re.IGNORECASE
)

# 已知卫视频道关键词（用于将误分到央视的卫视频道移回）
WEISHI_KEYWORDS = [
    '卫视', '凤凰', '东方卫视', '湖南卫视', '浙江卫视', '江苏卫视',
    '北京卫视', '东方', '深圳', '广东', '广州', '珠江',
]

# CCTV频道号提取正则
CCTV_NUM_PATTERN = re.compile(r'(?:CCTV|cctv)[\s\-]?(\d+)', re.IGNORECASE)

# CCTV频道名称到排序号的映射（用于非纯数字的CCTV频道）
CCTV_NAME_ORDER = {
    '综合': 1, '财经': 2, '综艺': 3, '中文国际': 4,
    '体育': 5, '电影': 6, '军事': 7, '农业': 7,
    '纪录': 9, '科教': 10, '戏曲': 11, '社会与法': 12,
    '新闻': 13, '少儿': 14, '音乐': 15, '4K': 16,
}

# 卫视预定义排序
WEISHI_ORDER = [
    '北京卫视', '东方卫视', '天津卫视', '重庆卫视',
    '湖南卫视', '浙江卫视', '江苏卫视', '广东卫视',
    '深圳卫视', '山东卫视', '河南卫视', '河北卫视',
    '湖北卫视', '四川卫视', '安徽卫视', '江西卫视',
    '福建卫视', '辽宁卫视', '吉林卫视', '黑龙江卫视',
    '陕西卫视', '甘肃卫视', '青海卫视', '云南卫视',
    '贵州卫视', '广西卫视', '山西卫视', '内蒙古卫视',
    '新疆卫视', '西藏卫视', '宁夏卫视', '海南卫视',
    '东南卫视', '珠江频道', '凤凰中文', '凤凰资讯',
]


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    print(line, end="")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_block_pattern():
    escaped = [ch.replace("(", "\\(").replace(")", "\\)") for ch in BLOCK_CHANNELS]
    return re.compile("^(" + "|".join(escaped) + "),", re.IGNORECASE)


def quality_score(name, url):
    """画质评分（越高越好）"""
    score = 50
    nl = name.lower()
    ul = url.lower()
    if '4k' in nl or 'uhd' in nl:
        score = 100
    elif 'fhd' in nl or '1080' in ul:
        score = 90
    elif any(k in nl for k in ['hd', '高清', '超清']) or '720' in ul:
        score = 80
    elif any(k in nl for k in ['sd', '标清']) or '480' in ul:
        score = 40
    elif any(k in nl for k in ['ld', '低清', '流畅']) or '360' in ul:
        score = 20
    for tag in ['/_sd/', '/sd/', '/_sd', '/sd', '_sd.m3u8']:
        if tag in ul:
            score = min(score, 40)
    for tag in ['/_hd/', '/hd/', '/_hd', '/hd', '_hd.m3u8']:
        if tag in ul:
            score = max(score, 70)
    for tag in ['/_fhd/', '/fhd/']:
        if tag in ul:
            score = max(score, 90)
    return score


def speed_test(url, timeout=5):
    """测试URL响应速度，返回(是否可用, 响应时间ms)"""
    try:
        start = time.time()
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        elapsed = int((time.time() - start) * 1000)
        return resp.status_code == 200, elapsed
    except Exception:
        return False, 99999


def clean_source(content):
    """清理直播源"""
    block_pattern = build_block_pattern()
    lines = content.strip().split("\n")
    cleaned = []
    removed = 0
    for line in lines:
        if block_pattern.match(line):
            removed += 1
            continue
        if any(dead in line for dead in DEAD_URLS):
            removed += 1
            continue
        cleaned.append(line)
    return "\n".join(cleaned) + "\n", removed


def parse_source(content):
    """解析TXT直播源为 {分类: [(频道名, URL)]} 字典"""
    genre_channels = OrderedDict()
    current_genre = None
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if ',#genre#' in line:
            current_genre = line.split(',#genre#')[0].strip()
            if current_genre not in genre_channels:
                genre_channels[current_genre] = []
            continue
        if current_genre and ',' in line:
            parts = line.split(',', 1)
            name = parts[0].strip()
            url = parts[1].strip() if len(parts) > 1 else ''
            if url.startswith('http://') or url.startswith('https://'):
                genre_channels[current_genre].append((name, url))
    return genre_channels


def is_cctv_channel(name):
    """判断是否为CCTV频道"""
    return bool(CCTV_PATTERN.match(name))


def is_weishi_channel(name):
    """判断是否为卫视频道"""
    if is_cctv_channel(name):
        return False
    return any(kw in name for kw in WEISHI_KEYWORDS) or '卫视' in name


def get_cctv_sort_key(name):
    """
    获取CCTV频道的排序键。
    CCTV1 -> 1, CCTV2 -> 2, ..., CCTV-5+ -> 5.5
    """
    m = CCTV_NUM_PATTERN.search(name)
    if m:
        num = int(m.group(1))
        # 处理 CCTV5+ 这种情况
        if '+' in name or '赛事' in name:
            return num + 0.5
        return num
    # 非数字CCTV频道（如"CCTV综合"），按名称映射
    for cname, order in CCTV_NAME_ORDER.items():
        if cname in name:
            return order
    return 999  # 未知CCTV频道排最后


def normalize_channel_name(name):
    """
    归一化频道名称，用于将同一频道的不同写法归为一组。
    例如 "CCTV-1 综合", "CCTV1综合", "CCTV1-综合" 都归为同一频道。
    """
    nl = name.upper().strip()
    # 去除画质后缀
    for suffix in ['4K', 'UHD', 'FHD', '1080P', '1080I', '720P', 'HD', 'SD',
                   '高清', '超清', '标清', '蓝光']:
        nl = nl.replace(suffix, '')
    # 去除多余空格和分隔符
    nl = re.sub(r'[\s\-]+', '', nl)
    return nl.strip()


def get_weishi_sort_key(name):
    """获取卫视频道的排序键"""
    for i, ws in enumerate(WEISHI_ORDER):
        if ws in name or name in ws:
            return i
    return len(WEISHI_ORDER)  # 未知卫视排最后


# 省级/城市地区关键词映射（频道名 → 地区标签）
# 按出现频率排序，前面优先匹配
REGION_KEYWORDS = [
    # 直辖市
    ("北京", ["北京"]),
    ("上海", ["上海"]),
    ("天津", ["天津"]),
    ("重庆", ["重庆"]),
    # 省级
    ("浙江", ["浙江"]),
    ("江苏", ["江苏"]),
    ("广东", ["广东", "珠江"]),
    ("山东", ["山东"]),
    ("河南", ["河南"]),
    ("湖北", ["湖北"]),
    ("湖南", ["湖南"]),
    ("四川", ["四川"]),
    ("安徽", ["安徽"]),
    ("河北", ["河北"]),
    ("福建", ["福建"]),
    ("辽宁", ["辽宁"]),
    ("陕西", ["陕西"]),
    ("江西", ["江西"]),
    ("云南", ["云南"]),
    ("山西", ["山西"]),
    ("广西", ["广西"]),
    ("贵州", ["贵州"]),
    ("海南", ["海南"]),
    ("甘肃", ["甘肃"]),
    ("吉林", ["吉林"]),
    ("黑龙江", ["黑龙江"]),
    ("内蒙古", ["内蒙古"]),
    ("新疆", ["新疆"]),
    ("西藏", ["西藏"]),
    ("宁夏", ["宁夏"]),
    ("青海", ["青海"]),
    ("深圳", ["深圳"]),
    ("杭州", ["杭州"]),
    ("南京", ["南京"]),
    ("成都", ["成都"]),
    ("武汉", ["武汉"]),
    ("广州", ["广州"]),
    ("长沙", ["长沙"]),
    ("西安", ["西安"]),
    ("郑州", ["郑州"]),
    ("沈阳", ["沈阳"]),
    ("济南", ["济南"]),
    ("福州", ["福州"]),
    ("合肥", ["合肥"]),
    ("南昌", ["南昌"]),
    ("贵阳", ["贵阳"]),
    ("昆明", ["昆明"]),
    ("哈尔滨", ["哈尔滨"]),
    ("石家庄", ["石家庄"]),
    ("太原", ["太原"]),
    ("南宁", ["南宁"]),
    ("兰州", ["兰州"]),
    ("长春", ["长春"]),
    ("呼和浩特", ["呼和浩特"]),
    ("银川", ["银川"]),
    ("乌鲁木齐", ["乌鲁木齐"]),
    ("拉萨", ["拉萨"]),
    ("西宁", ["西宁"]),
    ("海口", ["海口"]),
]

# 地区排序（常用地区在前，自定义地区在后，其余未知归为一类）
REGION_ORDER = [
    "北京", "上海", "浙江", "江苏", "广东", "山东", "河南", "湖北", "湖南",
    "四川", "安徽", "河北", "福建", "辽宁", "陕西", "江西", "云南", "山西",
    "广西", "贵州", "海南", "甘肃", "吉林", "黑龙江", "内蒙古", "新疆",
    "西藏", "宁夏", "青海", "深圳", "杭州", "南京", "成都", "武汉", "广州",
    "长沙", "西安", "郑州", "其他",
]


def classify_channels(genre_channels):
    """
    严格重新分类：
    - CCTV频道 → 央视频道
    - 卫视频道 → 卫视频道
    - 数字频道 → 按省份/城市细分
    - 港澳台 → 港澳台
    """
    cctv_channels = []
    weishi_channels = []
    digital_channels = []
    hkmt_channels = []

    for genre, channels in genre_channels.items():
        for name, url in channels:
            if is_cctv_channel(name):
                cctv_channels.append((name, url))
            elif genre == '卫视频道' or is_weishi_channel(name):
                weishi_channels.append((name, url))
            elif genre == '港澳台':
                hkmt_channels.append((name, url))
            elif genre == '央视频道':
                # 在央视分类里但不是CCTV的，检查是否是卫视
                if is_weishi_channel(name):
                    weishi_channels.append((name, url))
                else:
                    digital_channels.append((name, url))
            else:
                # 其他分类（数字频道等）
                digital_channels.append((name, url))

    # 将数字频道按地区细分
    regions = OrderedDict()

    for name, url in digital_channels:
        region = detect_region(name)
        if region not in regions:
            regions[region] = []
        regions[region].append((name, url))

    result = OrderedDict()
    if cctv_channels:
        result['央视频道'] = cctv_channels
    if weishi_channels:
        result['卫视频道'] = weishi_channels

    # 按预定义顺序输出地区分类
    for region in REGION_ORDER:
        if region in regions:
            result[region] = regions[region]

    # 补充可能出现的未定义地区
    for region, channels in regions.items():
        if region not in result:
            result[region] = channels

    if hkmt_channels:
        result['港澳台'] = hkmt_channels

    return result


def detect_region(name):
    """
    根据频道名称检测所属地区。
    返回地区标签，匹配不到则返回"其他"。
    """
    for region, keywords in REGION_KEYWORDS:
        for kw in keywords:
            if kw in name:
                return region
    return "其他"


def sort_channels_by_group(channels, get_sort_key_func):
    """
    将频道按名称分组，组内按分辨率（画质）降序排列，
    组间按 get_sort_key_func 提供的顺序排列。

    返回排好序的 [(name, url)] 列表。
    """
    # 按归一化名称分组
    groups = defaultdict(list)
    group_sort_keys = {}

    for name, url in channels:
        norm = normalize_channel_name(name)
        groups[norm].append((name, url))
        # 用第一个遇到的原始名取排序键
        if norm not in group_sort_keys:
            group_sort_keys[norm] = get_sort_key_func(name)

    # 按排序键对组排序
    sorted_group_keys = sorted(groups.keys(), key=lambda k: group_sort_keys[k])

    result = []
    for gk in sorted_group_keys:
        items = groups[gk]
        # 组内按画质降序排列
        items.sort(key=lambda x: -quality_score(x[0], x[1]))
        result.extend(items)

    return result


def download_zqtv(zip_url):
    """下载并解密朱雀TV直播源"""
    tmp_zip = "/tmp/zqtv_source.zip"
    tmp_dir = "/tmp/zqtv_extract"
    log(f"朱雀TV: 正在下载 {zip_url}")
    try:
        resp = requests.get(zip_url, timeout=60)
        if resp.status_code != 200:
            log(f"朱雀TV: 下载失败 HTTP {resp.status_code}")
            return None
    except Exception as e:
        log(f"朱雀TV: 下载失败 {e}")
        return None
    with open(tmp_zip, "wb") as f:
        f.write(resp.content)
    log(f"朱雀TV: 下载完成 {len(resp.content)} 字节")
    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(tmp_dir, pwd=ZQTV_PASSWORD.encode())
    except RuntimeError as e:
        err_msg = str(e)
        if "Bad password" in err_msg or "password" in err_msg.lower() or "decrypt" in err_msg.lower():
            log(f"⚠️ 解密密码已变更！当前密码解密失败，需要更新 ZQTV_PASSWORD")
            log(f"⚠️ 错误详情: {err_msg}")
        else:
            log(f"朱雀TV: 解密失败: {e}")
        return None
    except Exception as e:
        log(f"朱雀TV: 解密失败: {e}")
        return None
    src_path = os.path.join(tmp_dir, "source.txt")
    if not os.path.exists(src_path):
        log("朱雀TV: source.txt 不存在")
        return None
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(tmp_zip)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    cleaned, removed = clean_source(content)
    log(f"朱雀TV: 清理删除 {removed} 条")
    return cleaned


def download_wangzi():
    """下载王子电视直播源（明文）"""
    log(f"王子电视: 正在下载 {WANGZI_SOURCE_URL}")
    try:
        resp = requests.get(WANGZI_SOURCE_URL, timeout=60)
        if resp.status_code not in (200, 206):
            log(f"王子电视: 下载失败 HTTP {resp.status_code}")
            return None
    except Exception as e:
        log(f"王子电视: 下载失败 {e}")
        return None
    log(f"王子电视: 下载完成 {len(resp.content)} 字节")
    text = resp.content.decode('utf-8', errors='replace')
    cleaned, removed = clean_source(text)
    log(f"王子电视: 清理删除 {removed} 条")
    return cleaned


def extract_wangzi_cctv(wangzi_content):
    """从王子电视提取央视和卫视频道（严格分类）"""
    data = parse_source(wangzi_content)
    cctv = []
    weishi = []
    for genre, channels in data.items():
        for name, url in channels:
            if is_cctv_channel(name):
                cctv.append((name, url))
            elif genre == '卫视频道' or is_weishi_channel(name):
                weishi.append((name, url))
    log(f"王子电视: CCTV {len(cctv)} 条, 卫视 {len(weishi)} 条")
    return cctv, weishi


def merge_and_sort(zqtv_content, wangzi_cctv, wangzi_weishi):
    """
    合并朱雀TV和王子电视源。
    1. 严格重新分类（CCTV归央视，卫视归卫视）
    2. 央视按频道号排序（CCTV1, CCTV2, ...），同频道按分辨率降序
    3. 卫视按预定义顺序排序，同频道按分辨率降序
    4. 数字频道/港澳台按名称排序，同频道按分辨率降序
    5. 使用并发+缓存测速，首次运行约30秒，后续运行约2秒
    """
    zqtv = parse_source(zqtv_content)

    # 并发测速王子电视CCTV源（默认3秒超时，10并发）
    log("王子电视: 开始速度测试CCTV...")
    tested_cctv_results, cctv_cached = speed_test_batch(
        wangzi_cctv, timeout=3, max_workers=10)
    tested_cctv = [(n, u) for n, u, ms in tested_cctv_results]
    log(f"王子电视: CCTV {len(tested_cctv)}/{len(wangzi_cctv)} 条可用, "
        f"{cctv_cached} 条来自缓存")

    # 并发测速王子电视卫视源
    log("王子电视: 开始速度测试卫视...")
    tested_weishi_results, weishi_cached = speed_test_batch(
        wangzi_weishi, timeout=3, max_workers=10)
    tested_weishi = [(n, u) for n, u, ms in tested_weishi_results]
    log(f"王子电视: 卫视 {len(tested_weishi)}/{len(wangzi_weishi)} 条可用, "
        f"{weishi_cached} 条来自缓存")

    # 合并王子电视源到朱雀TV数据
    if '央视频道' not in zqtv:
        zqtv['央视频道'] = []
    if '卫视频道' not in zqtv:
        zqtv['卫视频道'] = []

    zqtv['央视频道'].extend(tested_cctv)
    zqtv['卫视频道'].extend(tested_weishi)

    # 严格重新分类
    classified = classify_channels(zqtv)
    log(f"重新分类完成: 央视 {len(classified.get('央视频道', []))} 条, "
        f"卫视 {len(classified.get('卫视频道', []))} 条, "
        f"港澳台 {len(classified.get('港澳台', []))} 条, "
        f"地区分类 {len([k for k in classified if k not in ('央视频道','卫视频道','港澳台')])} 个地区")

    # 分类内排序
    final = OrderedDict()

    # 央视：按频道号排序，同频道按分辨率降序
    if '央视频道' in classified:
        final['央视频道'] = sort_channels_by_group(
            classified['央视频道'], get_cctv_sort_key)

    # 卫视：按预定义顺序排序，同频道按分辨率降序
    if '卫视频道' in classified:
        final['卫视频道'] = sort_channels_by_group(
            classified['卫视频道'], get_weishi_sort_key)

    # 港澳台：按名称排序，同频道按分辨率降序
    if '港澳台' in classified:
        final['港澳台'] = sort_channels_by_group(
            classified['港澳台'], lambda name: name)

    # 所有地区分类（北京、上海、浙江等）：按名称排序，同频道按分辨率降序
    # 先收集所有地区分类的键（保持REGION_ORDER顺序）
    region_keys_in_order = [k for k in REGION_ORDER if k in classified]
    # 补充未定义的地区
    for key in classified:
        if key not in ('央视频道', '卫视频道', '港澳台') and key not in region_keys_in_order:
            region_keys_in_order.append(key)

    for key in region_keys_in_order:
        final[key] = sort_channels_by_group(
            classified[key], lambda name: name)

    # 写入
    output = []
    total = 0
    for genre, channels in final.items():
        output.append(f"{genre},#genre#")
        for name, url in channels:
            output.append(f"{name},{url}")
        output.append("")
        total += len(channels)

    log(f"合并完成: {len(final)} 个分类, {total} 个频道")
    return "\n".join(output) + "\n"


def git_commit_and_push():
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        files = [f for f in [OUTPUT_FILE, STATE_FILE, WANGZI_STATE_FILE, LOG_FILE] if os.path.exists(f)]
        if not files:
            return
        subprocess.run(["git", "add"] + files, check=True)
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            log("没有需要提交的变更")
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"auto: 更新直播源 {timestamp}"], check=True)
        token = os.environ.get("GITHUB_TOKEN", "")
        repo_url = os.environ.get("GITHUB_REPOSITORY", "")
        if token and repo_url:
            push_url = f"https://x-access-token:{token}@github.com/{repo_url}.git"
            subprocess.run(["git", "push", push_url], check=True)
        else:
            subprocess.run(["git", "push"], check=True)
        log("已提交并推送到 GitHub")
    except subprocess.CalledProcessError as e:
        log(f"Git 操作失败: {e}")


def main():
    log("=" * 50)
    log("开始检测直播源更新")

    zqtv_content = None
    wangzi_cctv = []
    wangzi_weishi = []

    # 1. 朱雀TV
    log("-" * 30)
    log("检测朱雀TV")
    try:
        resp = requests.get(ZQTV_CONFIG_URL, timeout=15)
        resp.raise_for_status()
        config = resp.json()
    except Exception as e:
        log(f"朱雀TV: 无法获取配置 {e}")
    else:
        current_source = config.get("source", "")
        current_ver = config.get("ver", "")
        state = load_json(STATE_FILE)
        last_source = state.get("last_source", "")
        last_ver = state.get("last_ver", "")
        changed = (current_source != last_source and last_source) or (current_ver != last_ver and last_ver)

        if changed or not last_source:
            zqtv_content = download_zqtv(current_source)
            if zqtv_content:
                log("朱雀TV: 已下载")
        else:
            log("朱雀TV: 无变化")

        save_json(STATE_FILE, {
            "last_source": current_source,
            "last_ver": current_ver,
            "last_pubmsg": config.get("pubMsg", ""),
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    # 2. 王子电视
    log("-" * 30)
    log("检测王子电视")
    try:
        resp = requests.get(WANGZI_SOURCE_URL, timeout=15)
        resp.raise_for_status()
        content_hash = hash(resp.content.decode('utf-8', errors='replace'))
    except Exception as e:
        log(f"王子电视: 无法获取 {e}")
    else:
        state = load_json(WANGZI_STATE_FILE)
        last_hash = state.get("last_hash", "")
        if content_hash != last_hash:
            wangzi_raw = download_wangzi()
            if wangzi_raw:
                wangzi_cctv, wangzi_weishi = extract_wangzi_cctv(wangzi_raw)
                log("王子电视: 已下载")
        else:
            log("王子电视: 无变化")

        save_json(WANGZI_STATE_FILE, {
            "last_hash": content_hash,
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    # 3. 合并输出
    if zqtv_content or wangzi_cctv or wangzi_weishi:
        if not zqtv_content:
            if os.path.exists(OUTPUT_FILE):
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    zqtv_content = f.read()
            else:
                zqtv_content = ""

        merged = merge_and_sort(zqtv_content, wangzi_cctv, wangzi_weishi)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(merged)
        log(f"已保存到 {OUTPUT_FILE}")

    # 4. 提交
    git_commit_and_push()
    log("=" * 50)


if __name__ == "__main__":
    main()
