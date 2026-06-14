"""
朱雀TV 直播源自动更新脚本

"""

import json
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from urllib.parse import urljoin

import requests


# ========== 朱雀TV 配置 ==========
ZQTV_CONFIG_URL = "http://207.56.16.135:9999/zqtv/config.json"
ZQTV_PASSWORD = "DBhkhdnefkhfq,#%"

STATE_FILE = "state.json"
OUTPUT_FILE = "source.txt"
LOG_FILE = "update_log.md"


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
    "好易购", "广西乐思购", "湖南快乐购",

    # 广告/导视/理财等
    "福利多多", "美好生活", "家庭理财", "潍坊金融频道",
    "潍坊新时尚", "潍坊生殖健康", "潍坊文艺时尚",
    "商城新闻频道", "潍坊企业家",
    "安徽导视", "廊坊导视频道", "杭州导视纪录",

    # 其他不想保留的频道，可按需自行增删
    "书画频道", "中国天气",
]

# 已知失效地址关键字
DEAD_URLS = [
    "101.35.240.114:88",
]


# ========== 工具函数 ==========
def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    print(line, end="")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_block_pattern():
    if not BLOCK_CHANNELS:
        return None
    escaped = [re.escape(ch) for ch in BLOCK_CHANNELS]
    return re.compile(r"^(" + "|".join(escaped) + r"),", re.IGNORECASE)


def clean_source_keep_categories(content):
    """
    清理朱雀TV源，但完全保留源内原始分类结构。

    不做：
    - 不合并其他源
    - 不重新分类
    - 不排序
    - 不把CCTV/卫视/地方台重新拆分
    """
    block_pattern = build_block_pattern()
    output_lines = []
    removed = 0
    genre_count = 0
    channel_count = 0

    for raw_line in content.splitlines():
        line = raw_line.strip()

        # 保留空行，用于维持原始分类间隔
        if not line:
            output_lines.append("")
            continue

        # 分类行原样保留
        if ",#genre#" in line:
            output_lines.append(line)
            genre_count += 1
            continue

        # 频道行清理
        if block_pattern and block_pattern.match(line):
            removed += 1
            continue

        if any(dead in line for dead in DEAD_URLS):
            removed += 1
            continue

        output_lines.append(line)
        if "," in line:
            channel_count += 1

    # 去掉文件末尾过多空行，保证以单个换行结束
    cleaned = "\n".join(output_lines).strip() + "\n"
    log(f"朱雀TV: 保留原始分类 {genre_count} 个, 频道 {channel_count} 条, 清理删除 {removed} 条")
    return cleaned


def find_source_txt(extract_dir):
    """在解压目录中查找 source.txt。"""
    direct = os.path.join(extract_dir, "source.txt")
    if os.path.exists(direct):
        return direct

    for root, _, files in os.walk(extract_dir):
        for filename in files:
            if filename.lower() == "source.txt":
                return os.path.join(root, filename)

    return None


def download_zqtv(zip_url):
    """下载并解密朱雀TV直播源，返回清理后的 source.txt 内容。"""
    if not zip_url:
        log("朱雀TV: 配置中 source 为空")
        return None

    # 兼容配置里给相对路径的情况
    zip_url = urljoin(ZQTV_CONFIG_URL, zip_url)

    tmp_zip = "/tmp/zqtv_source.zip"
    tmp_dir = "/tmp/zqtv_extract"

    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

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
            zf.extractall(tmp_dir, pwd=ZQTV_PASSWORD.encode("utf-8"))
    except RuntimeError as e:
        err = str(e)
        if "password" in err.lower() or "decrypt" in err.lower():
            log("朱雀TV: 解密失败，可能是密码已变更")
        log(f"朱雀TV: 解密错误详情: {e}")
        return None
    except Exception as e:
        log(f"朱雀TV: 解压失败 {e}")
        return None

    src_path = find_source_txt(tmp_dir)
    if not src_path:
        log("朱雀TV: 解压后未找到 source.txt")
        return None

    try:
        with open(src_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(src_path, "r", encoding="gbk", errors="replace") as f:
            content = f.read()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        os.remove(tmp_zip)
    except Exception:
        pass

    return clean_source_keep_categories(content)


def git_commit_and_push():
    """GitHub Actions 中提交并推送更新。"""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)

        files = [f for f in [OUTPUT_FILE, STATE_FILE, LOG_FILE] if os.path.exists(f)]
        if not files:
            log("没有可提交文件")
            return

        subprocess.run(["git", "add"] + files, check=True)

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )

        if not result.stdout.strip():
            log("没有需要提交的变更")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"auto: 更新朱雀TV直播源 {timestamp}"], check=True)

        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")

        if token and repo:
            push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url], check=True)
        else:
            subprocess.run(["git", "push"], check=True)

        log("已提交并推送到 GitHub")
    except subprocess.CalledProcessError as e:
        log(f"Git 操作失败: {e}")


def main():
    log("=" * 50)
    log("开始更新朱雀TV直播源")
    log("模式: 只使用朱雀TV，分类完全保留网站源分类，不合并其他源")

    try:
        resp = requests.get(ZQTV_CONFIG_URL, timeout=15)
        resp.raise_for_status()
        config = resp.json()
    except Exception as e:
        log(f"朱雀TV: 无法获取配置 {e}")
        git_commit_and_push()
        log("=" * 50)
        return

    current_source = config.get("source", "")
    current_ver = config.get("ver", "")
    current_pubmsg = config.get("pubMsg", "")

    state = load_json(STATE_FILE)
    last_source = state.get("last_source", "")
    last_ver = state.get("last_ver", "")

    changed = current_source != last_source or current_ver != last_ver
    if changed:
        log("朱雀TV: 检测到源地址或版本变化")
    else:
        log("朱雀TV: 配置无变化，仍重新下载以确保输出只来自朱雀TV")

    zqtv_content = download_zqtv(current_source)

    if zqtv_content:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(zqtv_content)
        log(f"已保存到 {OUTPUT_FILE}")
    else:
        log("朱雀TV: 本次未生成 source.txt")

    save_json(STATE_FILE, {
        "last_source": current_source,
        "last_ver": current_ver,
        "last_pubmsg": current_pubmsg,
        "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    git_commit_and_push()
    log("=" * 50)


if __name__ == "__main__":
    main()
