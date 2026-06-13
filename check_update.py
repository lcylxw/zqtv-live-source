"""
朱雀TV 直播源自动更新脚本
用于 GitHub Actions 定时检测并更新直播源
"""
import json
import os
import re
import shutil
import subprocess
import zipfile
import requests
from datetime import datetime

CONFIG_URL = "http://207.56.16.135:9999/zqtv/config.json"
PASSWORD = "DBhkhdnefkhfq,#%"
STATE_FILE = "state.json"
OUTPUT_FILE = "source.txt"
LOG_FILE = "update_log.md"

BLOCK_CHANNELS = [
    # 购物频道
    "养生馆", "健康有约", "百姓健康", "中华特产", "INBM证券服务",
    "太原佰乐购", "快乐购", "上虞新商都", "优购物", "央广购物",
    "南方购物", "家有购物", "东方购物", "CCTV中视购物",
    "四川星空购物", "大连乐天购物", "山东居家购物", "成都每日购物",
    "河北三佳购物", "河南欢腾购物", "深圳宜和购物", "爱家购物",
    "西安乐购购物", "辽宁宜佳购物", "重庆时尚购物", "长沙嘉丽购物",
    "严选好物", "好物分享", "精品甄选", "健康甄选",
    "好易购", "广西乐思购",
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
    "西安商务资讯", "深圳众创TV", "GRTN健康频道",
    # 钓鱼/围棋/武术
    "四海钓鱼", "天元围棋", "快乐垂钓", "河北杂技",
    # 少儿
    "CCTV14少儿", "NewTV-黑莓动画", "优漫卡通",
    "北京卡酷少儿", "北京少儿", "卡酷少儿",
    "嘉佳卡通", "广东少儿", "浙江少儿", "甘肃少儿", "重庆少儿", "金鹰卡通",
    # 戏曲
    "CCTV11-戏曲", "CCTV11戏曲", "岭南戏曲", "陕西秦腔",
    # 音乐
    "CCTV-风云音乐", "CCTV15音乐",
]

DEAD_URLS = ["101.35.240.114:88"]


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    print(line, end="")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_source": "", "last_ver": "", "last_pubmsg": "", "last_check": ""}


def save_state(source, ver, pubmsg):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_source": source,
            "last_ver": ver,
            "last_pubmsg": pubmsg,
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=2)


def clean_source(content):
    escaped = [ch.replace("(", "\\(").replace(")", "\\)") for ch in BLOCK_CHANNELS]
    block_pattern = re.compile("^(" + "|".join(escaped) + "),", re.IGNORECASE)
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
    log(f"清理: 删除 {removed} 条, 剩余 {len(cleaned)} 行")
    return "\n".join(cleaned) + "\n"


def download_and_decrypt(zip_url):
    tmp_zip = "/tmp/zqtv_source.zip"
    tmp_dir = "/tmp/zqtv_extract"
    log(f"正在下载: {zip_url}")
    try:
        resp = requests.get(zip_url, timeout=60)
        if resp.status_code != 200:
            log(f"下载失败: HTTP {resp.status_code}")
            return None
    except Exception as e:
        log(f"下载失败: {e}")
        return None
    with open(tmp_zip, "wb") as f:
        f.write(resp.content)
    log(f"下载完成: {len(resp.content)} 字节")
    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(tmp_dir, pwd=PASSWORD.encode())
    except RuntimeError as e:
        err_msg = str(e)
        if "Bad password" in err_msg or "password" in err_msg.lower() or "decrypt" in err_msg.lower():
            log(f"⚠️ 解密密码已变更！当前密码解密失败，需要更新 PASSWORD")
            log(f"⚠️ 错误详情: {err_msg}")
        else:
            log(f"解密失败: {e}")
        return None
    except Exception as e:
        log(f"解密失败: {e}")
        return None
    src_path = os.path.join(tmp_dir, "source.txt")
    if not os.path.exists(src_path):
        log("source.txt 不存在")
        return None
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(tmp_zip)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return clean_source(content)


def git_commit_and_push():
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        files_to_add = [f for f in [OUTPUT_FILE, STATE_FILE, LOG_FILE] if os.path.exists(f)]
        if not files_to_add:
            return
        subprocess.run(["git", "add"] + files_to_add, check=True)
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
    log("开始检测朱雀TV直播源更新")
    try:
        resp = requests.get(CONFIG_URL, timeout=15)
        resp.raise_for_status()
        config = resp.json()
    except Exception as e:
        log(f"无法获取 config.json: {e}")
        return

    current_source = config.get("source", "")
    current_ver = config.get("ver", "")
    current_pubmsg = config.get("pubMsg", "")
    log(f"版本: {current_ver}, 公告: {current_pubmsg}")

    state = load_state()
    last_source = state.get("last_source", "")
    last_ver = state.get("last_ver", "")

    changed = False
    if current_source != last_source and last_source:
        log(f"直播源地址变更!")
        changed = True
    if current_ver != last_ver and last_ver:
        log(f"版本号变更: {last_ver} -> {current_ver}")
        changed = True

    if changed or not last_source:
        reason = "检测到更新" if changed else "首次运行"
        log(f"{reason}，正在下载...")
        content = download_and_decrypt(current_source)
        if content:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            log(f"已保存到 {OUTPUT_FILE}")
    else:
        log("无变化，无需更新")

    save_state(current_source, current_ver, current_pubmsg)
    git_commit_and_push()
    log("检测完成")
    log("=" * 50)


if __name__ == "__main__":
    main()
