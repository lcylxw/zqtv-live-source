"""
朱雀TV 直播源自动更新脚本
用于 GitHub Actions 定时检测并更新直播源
合并王子电视CCTV/卫视源，按速度和画质排序
"""
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
import requests
from collections import OrderedDict
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

# 只保留这些分类
KEEP_GENRES = ['央视频道', '卫视频道', '数字频道', '港澳台']


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
    """画质评分"""
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
        if tag in ul: score = min(score, 40)
    for tag in ['/_hd/', '/hd/', '/_hd', '/hd', '_hd.m3u8']:
        if tag in ul: score = max(score, 70)
    for tag in ['/_fhd/', '/fhd/']:
        if tag in ul: score = max(score, 90)
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
    """从王子电视提取央视频道，分离CCTV和卫视"""
    data = parse_source(wangzi_content)
    cctv = []
    weishi = []
    for genre, channels in data.items():
        if genre == '央视频道':
            for name, url in channels:
                if name.upper().startswith('CCTV'):
                    cctv.append((name, url))
                else:
                    weishi.append((name, url))
    log(f"王子电视: CCTV {len(cctv)} 条, 卫视 {len(weishi)} 条")
    return cctv, weishi


def merge_and_sort(zqtv_content, wangzi_cctv, wangzi_weishi):
    """合并朱雀TV和王子电视源，按速度+画质排序"""
    zqtv = parse_source(zqtv_content)

    # 速度测试王子电视CCTV源
    log("王子电视: 开始速度测试CCTV...")
    tested_cctv = []
    for name, url in wangzi_cctv:
        ok, ms = speed_test(url, timeout=5)
        qs = quality_score(name, url)
        if ok:
            tested_cctv.append((name, url, qs, ms))
    log(f"王子电视: CCTV {len(tested_cctv)}/{len(wangzi_cctv)} 条可用")

    # 速度测试王子电视卫视源
    log("王子电视: 开始速度测试卫视...")
    tested_weishi = []
    for name, url in wangzi_weishi:
        ok, ms = speed_test(url, timeout=5)
        qs = quality_score(name, url)
        if ok:
            tested_weishi.append((name, url, qs, ms))
    log(f"王子电视: 卫视 {len(tested_weishi)}/{len(wangzi_weishi)} 条可用")

    # 合并到朱雀TV的央视频道
    if '央视频道' in zqtv:
        merged = []
        for name, url in zqtv['央视频道']:
            qs = quality_score(name, url)
            merged.append((name, url, qs, 9999, 'zqtv'))
        for name, url, qs, ms in tested_cctv:
            merged.append((name, url, qs, ms, 'wangzi'))
        merged.sort(key=lambda x: (-x[2], x[3]))
        zqtv['央视频道'] = [(n, u) for n, u, qs, ms, src in merged]

    # 合并到朱雀TV的卫视频道
    if '卫视频道' in zqtv:
        merged = []
        for name, url in zqtv['卫视频道']:
            qs = quality_score(name, url)
            merged.append((name, url, qs, 9999, 'zqtv'))
        for name, url, qs, ms in tested_weishi:
            merged.append((name, url, qs, ms, 'wangzi'))
        merged.sort(key=lambda x: (-x[2], x[3]))
        zqtv['卫视频道'] = [(n, u) for n, u, qs, ms, src in merged]

    # 只保留指定分类
    final = OrderedDict()
    for genre in KEEP_GENRES:
        if genre in zqtv:
            final[genre] = zqtv[genre]

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